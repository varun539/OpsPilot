import { startBackend } from "./backend";

export default async function globalSetup(): Promise<void> {
  await startBackend({ reset: true });
}
