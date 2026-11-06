// Called only by the authenticated server overview; never expose the token.
export async function readXCredits(token, fetcher = fetch) {
  const checked_at = new Date().toISOString();
  if (!token) return { status: "not_configured", checked_at };
  try {
    const response = await fetcher("https://api.x.com/2/usage/credits", {
      headers: { Authorization: `Bearer ${token}` },
      signal: AbortSignal.timeout(8000),
      redirect: "error",
      cache: "no-store",
    });
    if (!response.ok) throw new Error("Unavailable");
    const { data, errors } = await response.json();
    const amount = (v) => typeof v === "number" && Number.isFinite(v);
    if (
      errors?.length ||
      !data ||
      ![data.prepaid_balance, data.free_balance, data.total_balance].every(
        amount,
      ) ||
      data.free_balance < 0 ||
      data.total_balance < 0 ||
      !Array.isArray(data.free_grants) ||
      !data.free_grants.every(
        (g) =>
          amount(g.amount) &&
          g.amount >= 0 &&
          typeof g.expires_at === "string" &&
          Number.isFinite(Date.parse(g.expires_at)),
      )
    ) {
      throw new Error("Invalid balance");
    }
    return {
      status: "available",
      checked_at,
      prepaid_balance: data.prepaid_balance,
      free_balance: data.free_balance,
      total_balance: data.total_balance,
      free_grants: data.free_grants.map(({ amount, expires_at }) => ({
        amount,
        expires_at,
      })),
    };
  } catch {
    return { status: "unavailable", checked_at };
  }
}
