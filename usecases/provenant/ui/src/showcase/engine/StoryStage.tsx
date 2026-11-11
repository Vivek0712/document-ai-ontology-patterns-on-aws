/**
 * The frame layout of a comparison showcase: the story's header pinned at the top of the stage, the step's body
 * centred in the height that is left, so a short step sits near the caption bar instead of at the top of an empty
 * stage. A body taller than the stage scrolls (it is never clipped). Pure layout: frames stay functions of their
 * (payload, step, progress).
 */
import type { ReactNode } from "react";
import { cn } from "../../design";

export function StoryStage({ header, children, maxWidth = 1180, className }: { header?: ReactNode; children: ReactNode; maxWidth?: number; className?: string }) {
  return (
    <div className="h-full overflow-y-auto">
      <div className={cn("mx-auto flex min-h-full flex-col gap-3 px-4 py-4 sm:px-6", className)} style={{ maxWidth }}>
        {header}
        <div className="flex flex-1 flex-col justify-center gap-3" data-stage-body>
          {children}
        </div>
      </div>
    </div>
  );
}
