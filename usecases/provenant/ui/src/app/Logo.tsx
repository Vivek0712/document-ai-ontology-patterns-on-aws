/** The Provenant mark: a region boundary on a dark tile (the one place orange appears outside data). */
export function Logo({ size = 20 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden className="shrink-0">
      <rect width="32" height="32" rx="7" fill="#111113" />
      <rect x="7.5" y="9.5" width="17" height="13" rx="3" fill="none" stroke="#FF6200" strokeWidth="2.5" />
    </svg>
  );
}
