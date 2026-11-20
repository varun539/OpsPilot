import { stopBackend } from "./backend";

export default async function globalTeardown(): Promise<void> {
  await stopBackend();
}
