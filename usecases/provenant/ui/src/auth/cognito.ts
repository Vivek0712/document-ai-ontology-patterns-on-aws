/**
 * Amazon Cognito SRP sign-in (user pool rp-users, app client rp-web, no client secret).
 * Tokens live in memory only: a reload signs the user out.
 * API Gateway requires the ACCESS token (scope aws.cognito.signin.user.admin); an ID token gets 401.
 */
import {
  AuthenticationDetails,
  CognitoUser,
  CognitoUserPool,
  type CognitoUserSession,
  type ICognitoStorage,
} from "amazon-cognito-identity-js";
import type { RuntimeConfig } from "@contracts/api";

class MemoryStorage implements ICognitoStorage {
  private data = new Map<string, string>();
  setItem(key: string, value: string): void {
    this.data.set(key, value);
  }
  getItem(key: string): string | null {
    return this.data.get(key) ?? null;
  }
  removeItem(key: string): void {
    this.data.delete(key);
  }
  clear(): void {
    this.data.clear();
  }
}

export type SignInResult =
  | { kind: "signed_in" }
  | { kind: "new_password_required"; complete: (newPassword: string) => Promise<SignInResult> };

export class CognitoAuth {
  private readonly storage = new MemoryStorage();
  private readonly pool: CognitoUserPool;
  private user: CognitoUser | null = null;

  constructor(cfg: RuntimeConfig) {
    this.pool = new CognitoUserPool({
      UserPoolId: cfg.userPoolId,
      ClientId: cfg.userPoolClientId,
      Storage: this.storage,
    });
  }

  signIn(username: string, password: string): Promise<SignInResult> {
    const user = new CognitoUser({ Username: username, Pool: this.pool, Storage: this.storage });
    const details = new AuthenticationDetails({ Username: username, Password: password });
    return new Promise((resolve, reject) => {
      const callbacks = {
        onSuccess: () => {
          this.user = user;
          resolve({ kind: "signed_in" as const });
        },
        onFailure: (err: unknown) => reject(err instanceof Error ? err : new Error(String(err))),
        newPasswordRequired: () => {
          resolve({
            kind: "new_password_required",
            complete: (newPassword: string) =>
              new Promise<SignInResult>((res2, rej2) => {
                user.completeNewPasswordChallenge(newPassword, {}, {
                  onSuccess: () => {
                    this.user = user;
                    res2({ kind: "signed_in" });
                  },
                  onFailure: (err: unknown) => rej2(err instanceof Error ? err : new Error(String(err))),
                });
              }),
          });
        },
      };
      user.authenticateUser(details, callbacks);
    });
  }

  /** Current access token; the SDK refreshes it with the in-memory refresh token when it has expired. */
  accessToken(): Promise<string> {
    const user = this.user ?? this.pool.getCurrentUser();
    if (!user) return Promise.reject(new Error("not signed in"));
    return new Promise((resolve, reject) => {
      user.getSession((err: Error | null, session: CognitoUserSession | null) => {
        if (err || !session) reject(err ?? new Error("no session"));
        else resolve(session.getAccessToken().getJwtToken());
      });
    });
  }

  signOut(): void {
    this.user?.signOut();
    this.user = null;
    this.storage.clear();
  }
}

/** Kept here for compatibility; the shell imports it from ./config so Cognito loads only on sign-in. */
export { loadRuntimeConfig } from "./config";
