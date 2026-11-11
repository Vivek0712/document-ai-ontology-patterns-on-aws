/**
 * Session: runtime config, Cognito auth and the API client for the signed-in user.
 * VITE_API_MODE=mock|replay swaps in the fixture-backed MockApi (dev only; banner "mock data").
 */
import { createContext, type ReactNode, useCallback, useContext, useMemo, useState } from "react";
import type { Me, RuntimeConfig } from "@contracts/api";
import type { CognitoAuth, SignInResult } from "../auth/cognito";
import { readPref, writePref } from "../lib/storage";
import { HttpApi } from "./http";
import type { RpApi } from "./types";

export const API_MODE: "http" | "mock" | "replay" = __RP_API_MODE__;
/** Compile-time constant: false in the production build, so the mock code and fixtures are tree-shaken out. */
export const IS_MOCK = __RP_MOCK__;

interface Session {
  api: RpApi;
  me: Me;
}

interface SessionCtx {
  config?: RuntimeConfig;
  session?: Session;
  signIn(username: string, password: string): Promise<SignInResult>;
  /** mock mode only: sign in as fixture user a or b */
  signInMock(user: "a" | "b"): Promise<void>;
  signOut(): void;
  /**
   * The API as identity `user` (User A or User B), for screens that send the same request with each token (SC-7,
   * W-13 "Try as B"). The signed-in session when it is that user; in mock mode a second fixture session over the
   * same world; otherwise undefined (live mode holds one Cognito session until the second SRP sign-in lands).
   */
  apiAs(user: "a" | "b"): Promise<RpApi | undefined>;
  /** mock mode only */
  mockControls?: { fastForward(): void };
}

const Ctx = createContext<SessionCtx | null>(null);

/**
 * Mock mode: when the lending package's recorded ingest starts replaying. Remembered for 30 minutes so a reload
 * (or a deep link into a showcase) does not restart it; `?ingest=done` opens on a finished package and
 * `?ingest=live` restarts it now.
 */
function mockIngestStart(): number {
  const param = new URLSearchParams(window.location.search).get("ingest");
  const now = Date.now();
  const kept = Number(readPref("mockIngestAt"));
  const start = param === "done" ? now - 60 * 60_000 : param === "live" ? now : Number.isFinite(kept) && kept > 0 && now - kept < 30 * 60_000 ? kept : now;
  writePref("mockIngestAt", String(start));
  return start;
}

export function SessionProvider({ config, children }: { config?: RuntimeConfig; children: ReactNode }) {
  const [session, setSession] = useState<Session>();
  const [auth, setAuth] = useState<CognitoAuth>();
  const [mockControls, setMockControls] = useState<SessionCtx["mockControls"]>();

  const signIn = useCallback(
    async (username: string, password: string): Promise<SignInResult> => {
      if (!config) throw new Error("runtime config missing");
      // amazon-cognito-identity-js loads with the first sign-in, not with the shell
      const a = auth ?? new (await import("../auth/cognito")).CognitoAuth(config);
      setAuth(a);
      const finish = async (r: SignInResult): Promise<SignInResult> => {
        if (r.kind === "signed_in") {
          const api = new HttpApi(config.apiUrl.replace(/\/$/, ""), () => a.accessToken());
          const me = await api.me();
          setSession({ api, me });
          return r;
        }
        return { kind: "new_password_required", complete: async (pw) => finish(await r.complete(pw)) };
      };
      return finish(await a.signIn(username, password));
    },
    [auth, config],
  );

  const signInMock = useCallback(async (user: "a" | "b") => {
    if (!__RP_MOCK__) throw new Error("mock sign-in is dev only");
    const [{ MockApi }, { loadWorld }] = await Promise.all([
      import("../mocks/server"),
      import("../mocks/world"),
    ]);
    const world = await loadWorld();
    const api = new MockApi(world, user, API_MODE === "replay" ? "replay" : "mock", mockIngestStart());
    setMockControls({ fastForward: () => api.fastForward() });
    setSession({ api, me: await api.me() });
    writePref("mockUser", user);
  }, []);

  const apiAs = useCallback(
    async (user: "a" | "b"): Promise<RpApi | undefined> => {
      const me = session?.me;
      const isUser = (u: "a" | "b") => Boolean(me && (me.username === `user-${u}` || me.email?.startsWith(`user-${u}@`)));
      if (session && isUser(user)) return session.api;
      if (!__RP_MOCK__) return undefined;
      const [{ MockApi }, { loadWorld }] = await Promise.all([import("../mocks/server"), import("../mocks/world")]);
      return new MockApi(await loadWorld(), user, API_MODE === "replay" ? "replay" : "mock", Date.now() - 60 * 60_000);
    },
    [session],
  );

  const signOut = useCallback(() => {
    if (__RP_MOCK__) writePref("mockUser", "signed-out");
    auth?.signOut();
    setSession(undefined);
  }, [auth]);

  const value = useMemo(
    () => ({ config, session, signIn, signInMock, signOut, apiAs, mockControls }),
    [config, session, signIn, signInMock, signOut, apiAs, mockControls],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useSession(): SessionCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error("useSession outside SessionProvider");
  return c;
}

/** API client for the signed-in user (screens render only inside the signed-in shell). */
export function useApi(): RpApi {
  const s = useSession().session;
  if (!s) throw new Error("not signed in");
  return s.api;
}

export function useMe(): Me {
  const s = useSession().session;
  if (!s) throw new Error("not signed in");
  return s.me;
}
