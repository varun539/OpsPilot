#!/usr/bin/env node
/**
 * Verify that frontend/src/lib/api/generated.ts matches the FastAPI OpenAPI schema.
 *
 * The schema is exported from the running application code, then TypeScript
 * types are generated into a temporary file and compared byte-for-byte with the
 * committed generated file. A mismatch fails the check and prints how to fix it.
 */

import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const projectRoot = resolve(frontendRoot, "..");
const generatedPath = join(frontendRoot, "src", "lib", "api", "generated.ts");
const schemaPath = join(frontendRoot, "openapi.json");
const python =
  process.env.OPSPILOT_PYTHON || join(projectRoot, ".venv", "bin", "python");
const openapiTypescript = join(frontendRoot, "node_modules", ".bin", "openapi-typescript");

const temporaryDirectory = mkdtempSync(join(tmpdir(), "opspilot-api-types-"));
const temporaryGenerated = join(temporaryDirectory, "generated.ts");

try {
  execFileSync(python, [join(projectRoot, "scripts", "export-openapi.py")], {
    cwd: projectRoot,
    stdio: "pipe",
  });
  execFileSync(openapiTypescript, [schemaPath, "-o", temporaryGenerated], {
    cwd: frontendRoot,
    stdio: "pipe",
  });

  const expected = readFileSync(generatedPath, "utf8");
  const actual = readFileSync(temporaryGenerated, "utf8");

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
