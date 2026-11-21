#!/usr/bin/env node
/**
 * Verify that frontend/src/lib/api/generated.ts matches the FastAPI OpenAPI schema.
 *
 * The schema is exported from the running application code, then TypeScript
 * types are generated into a temporary file and compared byte-for-byte with the
 * committed generated file. A mismatch fails the check and prints how to fix it.
 */

import { execFileSync } from "node:child_process";
import { readFileSync, rmSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const projectRoot = resolve(frontendRoot, "..");
const generatedPath = join(frontendRoot, "src", "lib", "api", "generated.ts");
const schemaPath = join(frontendRoot, "openapi.json");

// Interpreter resolution: prefer OPSPILOT_PYTHON env var, then fallback to
// which python3, then which python. No hardcoded .venv/bin/python.
let python = process.env.OPSPILOT_PYTHON;
if (!python) {
  try {
    python = execFileSync("which python3", {
      encoding: "utf8",
      cwd: projectRoot,
    }).trim();
  } catch {
    // python3 not found, try python
  }
  if (!python) {
    try {
      python = execFileSync("which python", {
        encoding: "utf8",
        cwd: projectRoot,
      }).trim();
    } catch {
      python = undefined;
    }
  }
}
if (!python) {
  console.error(
    "Unable to locate Python interpreter. Set OPSPILOT_PYTHON to a valid python path.",
  );
  process.exitCode = 1;
  process.exit();
}

const temporaryDirectory = mkdtempSync(join(tmpdir(), "opspilot-api-types-"));
const temporaryGenerated = join(temporaryDirectory, "generated.ts");

try {
  // Export the OpenAPI schema from the FastAPI app
  execFileSync(python, [join(projectRoot, "scripts", "export-openapi.py")], {
    cwd: projectRoot,
    stdio: "pipe",
  });

  // Generate TypeScript types using openapi-typescript via npx.
  // Use execFileSync with shell: true to resolve npx properly.
  const npxResult = execFileSync("cmd.exe", [
    "/c",
    "npx openapi-typescript " + schemaPath + " -o " + temporaryGenerated,
  ], {
    cwd: frontendRoot,
    encoding: "utf8",
    stdio: "pipe",
    shell: true,
  }).trim();

  // Normalize line endings before comparing. Git's core.autocrlf checks out
  // text files with CRLF on Windows while openapi-typescript writes LF, so a
  // raw byte comparison would fail for line-ending reasons alone.
  const normalizeEol = (text) => text.replace(/\r\n/g, "\n");
  const expected = normalizeEol(readFileSync(generatedPath, "utf8"));
  const actual = normalizeEol(readFileSync(temporaryGenerated, "utf8"));

  if (expected !== actual) {
    console.error(
      [
        "Generated API types are out of date with the FastAPI OpenAPI schema.",
        "Run `npm run api:types` from frontend/ and commit the updated",
        "openapi.json and src/lib/api/generated.ts files.",
      ].join("\n"),
    );
    process.exitCode = 1;
  } else {
    console.log("Generated API types match the FastAPI OpenAPI schema.");
  }
} catch (error) {
  console.error("Unable to verify generated API types:", error.message ?? error);
  process.exitCode = 1;
} finally {
  rmSync(temporaryDirectory, { recursive: true, force: true });
}
