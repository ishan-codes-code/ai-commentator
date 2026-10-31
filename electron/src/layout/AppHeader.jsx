import {
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
} from "lucide-react";
import { Button } from "@/components/ui/button.jsx";
import { Separator } from "@/components/ui/separator.jsx";
import StatusIndicator from "@/primitives/StatusIndicator.jsx";
import useWorkerStatus from "@/hooks/useWorkerStatus.js";

const LABELS = {
  idle: "Idle",
  processing: "Processing",
  done: "Done",
  error: "Error",
  cancelled: "Cancelled",
};

export default function AppHeader({
  title,
  context,
  navigationCollapsed,
  onToggleNavigation,
  inspectorOpen = false,
  showInspectorToggle = false,
  onToggleInspector,
}) {
  const { status } = useWorkerStatus();
  const NavigationIcon = navigationCollapsed ? PanelLeftOpen : PanelLeftClose;
  const InspectorIcon = inspectorOpen ? PanelRightClose : PanelRightOpen;

  return (
    <header className="flex h-11 shrink-0 items-center justify-between gap-3 border-b border-border bg-surface px-3">
      <div className="flex min-w-0 items-center gap-2">
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          aria-label={navigationCollapsed ? "Expand navigation sidebar" : "Collapse navigation sidebar"}
          aria-controls="studio-navigation"
          aria-expanded={!navigationCollapsed}
          title={navigationCollapsed ? "Expand navigation sidebar" : "Collapse navigation sidebar"}
          onClick={onToggleNavigation}
        >
          <NavigationIcon aria-hidden />
        </Button>
        <Separator orientation="vertical" className="h-4" />
        <h1 className="truncate text-[13px] font-semibold tracking-[-0.01em] text-foreground">
          {title}
        </h1>
        {context ? (
          <span className="hidden truncate text-[11px] text-subtle sm:inline">
            {context}
          </span>
        ) : null}
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {showInspectorToggle ? (
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            aria-label={inspectorOpen ? "Close project inspector" : "Open project inspector"}
            aria-controls="project-inspector"
            aria-expanded={inspectorOpen}
            title={inspectorOpen ? "Close project inspector" : "Open project inspector"}
            onClick={onToggleInspector}
          >
            <InspectorIcon aria-hidden />
          </Button>
        ) : null}
        <span className="hidden text-xs text-subtle md:inline">Engine</span>
        <Separator orientation="vertical" className="hidden h-4 md:block" />
        <StatusIndicator status={status} label={LABELS[status] ?? status} />
      </div>
    </header>
  );
}
