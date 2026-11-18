/**
 * Manage the FastAPI application used by browser tests.
 *
 * The backend runs as a detached child process so both the Playwright global
 * setup/teardown and individual test workers can stop and restart it using a
 * PID file. A restart keeps the same SQLite application and checkpoint
 * databases, which is how restart recovery is verified in the browser.
 */

import { spawn } from "node:child_process";
import { rmSync, readFileSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";

// Playwright loads global setup as CommonJS, so avoid import.meta. The suite is
// always invoked from the frontend directory (npm run test:e2e).
export const projectRoot = resolve(
  process.env.OPSPILOT_E2E_PROJECT_ROOT ?? resolve(process.cwd(), ".."),
);

export const API_URL = "http://127.0.0.1:8000";
const APP_DB = "/tmp/opspilot-e2e-app.sqlite";
const CHECKPOINT_DB = "/tmp/opspilot-e2e-checkpoints.sqlite";
const PID_FILE = "/tmp/opspilot-e2e-backend.pid";

function pythonExecutable(): string {
  return process.env.OPSPILOT_PYTHON || join(projectRoot, ".venv", "bin", "python");
}

function readPid(): number | null {
  try {
    const value = Number.parseInt(readFileSync(PID_FILE, "utf8").trim(), 10);
    return Number.isFinite(value) && value > 0 ? value : null;
  } catch {
    return null;
  }
}

export async function backendHealthy(): Promise<boolean> {
  try {
    const response = await fetch(`${API_URL}/health`, {
      signal: AbortSignal.timeout(1_000),
    });
    return response.ok;
  } catch {
    return false;
  }
}

async function waitForHealth(timeoutMs = 60_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await backendHealthy()) return;
    await new Promise((sleep) => setTimeout(sleep, 250));
  }
  throw new Error(`E2E backend did not become healthy at ${API_URL}/health`);
}

async function waitForShutdown(timeoutMs = 15_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (!(await backendHealthy())) return;
    await new Promise((sleep) => setTimeout(sleep, 200));
  }
}

function removeDatabaseFiles(): void {
  for (const path of [APP_DB, CHECKPOINT_DB]) {
    for (const candidate of [path, `${path}-wal`, `${path}-shm`]) {
      rmSync(candidate, { force: true });
    }
  }
}

export async function startBackend(options: { reset?: boolean } = {}): Promise<void> {
  if (options.reset) {
    removeDatabaseFiles();
  }
  if (await backendHealthy()) {
    if (readPid() === null) {
      throw new Error(
        "Port 8000 is already serving an unknown backend. Stop it before running E2E tests.",
      );
    }
    return;
  }

  const child = spawn(
    pythonExecutable(),
    [
      "-m",
      "uvicorn",
      "tests.e2e_backend:app",
      "--host",
      "127.0.0.1",
      "--port",
      "8000",
      "--log-level",
      "warning",
    ],
    {
      cwd: projectRoot,
      env: {
        ...process.env,
        PYTHONPATH: projectRoot,
        OPSPILOT_E2E_APP_DB: APP_DB,
        OPSPILOT_E2E_CHECKPOINT_DB: CHECKPOINT_DB,
        ...(options.reset ? {} : { OPSPILOT_E2E_PRESERVE: "1" }),
      },
      detached: true,
      stdio: "ignore",
    },
  );
  if (child.pid === undefined) {
    throw new Error("Unable to start the E2E backend process");
  }
  writeFileSync(PID_FILE, String(child.pid), "utf8");
  child.unref();

  try {
    await waitForHealth();
  } catch (error) {
    await stopBackend();
    throw error;
  }
}

function killProcessGroup(pid: number, signal: NodeJS.Signals): void {
  try {
    process.kill(-pid, signal);
  } catch {
    try {
      process.kill(pid, signal);
    } catch {
      // Already stopped.
    }
  }
}

export async function stopBackend(): Promise<void> {
  const pid = readPid();
  if (pid === null) return;
  killProcessGroup(pid, "SIGTERM");
  await waitForShutdown();
  if (await backendHealthy()) {
    killProcessGroup(pid, "SIGKILL");
    await waitForShutdown(5_000);
  }
  rmSync(PID_FILE, { force: true });
}

/** Stop and restart the API against the same SQLite files and checkpoints. */
export async function restartBackend(): Promise<void> {
  await stopBackend();
  await startBackend();
}
