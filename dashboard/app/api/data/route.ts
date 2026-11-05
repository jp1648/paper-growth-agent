import { authenticated } from "../../../lib/auth";
import { overview, trace } from "../../../lib/db";
export const dynamic = "force-dynamic";
export async function GET(request: Request) {
  if (!(await authenticated()))
    return Response.json({ error: "Authentication required" }, { status: 401 });
  const run = new URL(request.url).searchParams.get("run");
  if (run && run.length > 200)
    return Response.json({ error: "Invalid run" }, { status: 400 });
  try {
    return Response.json(
      run ? { events: await trace(run) } : await overview(),
      {
        headers: { "Cache-Control": "private, no-store" },
      },
    );
  } catch {
    return Response.json(
      {
        error:
          "Database unavailable. Check the server connection and telemetry exporter.",
      },
      { status: 503 },
    );
  }
}
