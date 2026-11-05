import { redirect } from "next/navigation";
import { authenticated } from "../lib/auth";
import { overview } from "../lib/db";
import Observatory from "../components/Observatory";
import type { OverviewData } from "../lib/types";
export const dynamic = "force-dynamic";
export default async function Home() {
  if (!(await authenticated())) redirect("/login");
  let initial: OverviewData = { snapshot: null, at: null };
  let error = "";
  try {
    initial = await overview();
  } catch {
    error =
      "Database unavailable. Check the server connection and telemetry exporter.";
  }
  return <Observatory initial={initial} initialError={error} />;
}
