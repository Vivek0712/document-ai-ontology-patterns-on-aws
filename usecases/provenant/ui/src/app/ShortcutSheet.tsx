import { Dialog, Kbd } from "../design";
import { SHORTCUTS } from "./shortcuts";

export function ShortcutSheet({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="Keyboard shortcuts" className="w-[min(420px,calc(100vw-32px))]">
      <div className="border-b border-border px-4 py-3 text-md font-medium">Keyboard shortcuts</div>
      <ul className="p-2">
        {SHORTCUTS.map(([keys, label]) => (
          <li key={label} className="flex h-8 items-center justify-between rounded-sm px-2 text-sm text-text-2">
            <span>{label}</span>
            <span className="flex gap-1">
              {keys.map((k) => (
                <Kbd key={k}>{k}</Kbd>
              ))}
            </span>
          </li>
        ))}
      </ul>
    </Dialog>
  );
}
