/** localStorage for per-viewer conveniences only (theme, last package, sidebar). Every access may throw. */
export function readPref(key: string): string | undefined {
  try {
    return localStorage.getItem(`rp.${key}`) ?? undefined;
  } catch {
    return undefined;
  }
}

export function writePref(key: string, value: string | undefined): void {
  try {
    if (value === undefined) localStorage.removeItem(`rp.${key}`);
    else localStorage.setItem(`rp.${key}`, value);
  } catch {
    /* private window or blocked storage: the preference is simply not remembered */
  }
}
