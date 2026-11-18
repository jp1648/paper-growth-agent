import { login } from "./actions";
export const dynamic = "force-dynamic";
export default async function Login({
  searchParams,
}: {
  searchParams: Promise<{ error?: string }>;
}) {
  const { error } = await searchParams;
  const configured = (process.env.DASHBOARD_PASSWORD?.length || 0) >= 24;
  return (
    <main className="login">
      <div className="login-card">
        <div className="brand">
          <span className="brand-mark">↗</span> trader
          <span className="muted"> / observatory</span>
        </div>
        <div className="eyebrow">PRIVATE WORKSPACE</div>
        <h1>
          A clear view of
          <br />
          every decision.
        </h1>
        <p>
          Performance, evidence and execution.
          <br />
          Your paper agent, made observable.
        </p>
        {configured ? (
          <form action={login}>
            <label htmlFor="password">Workspace password</label>
            <input
              id="password"
              name="password"
              type="password"
              required
              autoComplete="current-password"
            />
            {error && (
              <p role="alert" className="error">
                {error === "limited"
                  ? "Too many sign-in attempts. Try again in one minute."
                  : "Unable to sign in. Check your password."}
              </p>
            )}
            <button className="primary" type="submit">
              Enter observatory <span>↗</span>
            </button>
          </form>
        ) : (
          <p role="alert" className="error">
            Set a DASHBOARD_PASSWORD of at least 24 characters on the server to
            enable access.
          </p>
        )}
        <div className="login-foot">PAPER TRADING ONLY · READ-ONLY ACCESS</div>
      </div>
    </main>
  );
}
