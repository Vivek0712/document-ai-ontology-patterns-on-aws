import { type FormEvent, useState } from "react";
import { IS_MOCK, useSession } from "../../../api/context";
import type { SignInResult } from "../../../auth/cognito";
import { ErrorBox } from "../components/ui";

/** Sign-in. Real app: Cognito SRP form. Mock mode: pick fixture user A or B. */
export function SignInScreen() {
  const { signIn, signInMock, config } = useSession();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [newPw, setNewPw] = useState("");
  const [pending, setPending] = useState<Extract<SignInResult, { kind: "new_password_required" }>>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(undefined);
    try {
      await fn();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      if (pending) {
        const r = await pending.complete(newPw);
        if (r.kind === "new_password_required") setPending(r);
        return;
      }
      const r = await signIn(username.trim(), password);
      if (r.kind === "new_password_required") setPending(r);
    });
  };

  const demoUsers = config?.demoUsers || new URLSearchParams(window.location.search).get("demoUsers") === "1";

  return (
    <div className="signin">
      <div className="card stack" style={{ gap: 18 }}>
        <div>
          <h1>Sign in</h1>
          <div className="muted">Provenant · Every answer, provenance included. · fictitious data</div>
        </div>
        {IS_MOCK ? (
          <div className="stack">
            <div className="fine">Mock mode: no Cognito. Choose a fixture user.</div>
            <button className="btn userbtn" disabled={busy} onClick={() => void run(() => signInMock("a"))}>
              user-a@example.com · lending-analyst
            </button>
            <button className="btn userbtn ghost" disabled={busy} onClick={() => void run(() => signInMock("b"))}>
              user-b@example.com · markets-analyst
            </button>
          </div>
        ) : (
          <form className="stack" onSubmit={submit}>
            {demoUsers && !pending && (
              <div className="row">
                <button type="button" className="btn small ghost" onClick={() => setUsername("user-a@example.com")}>user-a · lending-analyst</button>
                <button type="button" className="btn small ghost" onClick={() => setUsername("user-b@example.com")}>user-b · markets-analyst</button>
              </div>
            )}
            {pending ? (
              <label className="field">
                New password (first sign-in)
                <input className="input" type="password" autoComplete="new-password" value={newPw} onChange={(e) => setNewPw(e.target.value)} required minLength={8} />
              </label>
            ) : (
              <>
                <label className="field">
                  Email or username
                  <input className="input" autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} required />
                </label>
                <label className="field">
                  Password
                  <input className="input" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
                </label>
              </>
            )}
            <button className="btn" disabled={busy} type="submit">
              {busy ? "signing in…" : pending ? "Set password" : "Sign in"}
            </button>
          </form>
        )}
        <ErrorBox error={error} title="sign-in failed" />
        <div className="fine">
          Amazon Cognito user pool rp-users{config ? ` · ${config.region}` : ""} · tokens are kept in memory only (a reload signs you out)
        </div>
      </div>
    </div>
  );
}
