import { useCallback, useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import AppLayout from "@/layout/AppLayout.jsx";
import AppHeader from "@/layout/AppHeader.jsx";
import Sidebar from "@/layout/Sidebar.jsx";
import DashboardSection from "@/app/sections/DashboardSection.jsx";
import MatchesSection from "@/app/sections/MatchesSection.jsx";
import CommentarySection from "@/app/sections/CommentarySection.jsx";
import SettingsSection from "@/app/sections/SettingsSection.jsx";
import WorkspaceSection from "@/app/sections/WorkspaceSection.jsx";
import StudioPlaceholder from "@/app/sections/StudioPlaceholder.jsx";
import { MOTION, EASING } from "@/lib/tokens.js";

const SECTION_VIEWS = {
  dashboard: DashboardSection,
  matches: MatchesSection,
  commentary: CommentarySection,
  settings: SettingsSection,
  prompts: StudioPlaceholder,
  audio: StudioPlaceholder,
};

const SECTION_META = {
  dashboard: { title: "Dashboard", context: "Project library" },
  matches: { title: "AI Analysis", context: "Validated observations and events" },
  commentary: { title: "Commentary", context: "Generated lines and scripts" },
  settings: { title: "Settings", context: "Application preferences" },
  prompts: { title: "AI Prompts", context: "Prompt inspection is planned" },
  audio: { title: "Audio", context: "Playback lanes are not available yet" },
};

export default function App() {
  const [activeSection, setActiveSection] = useState("dashboard");
  const [workspaceId, setWorkspaceId] = useState(null);
  const [projectName, setProjectName] = useState(null);
  const [navigationCollapsed, setNavigationCollapsed] = useState(false);
  const [inspectorOpen, setInspectorOpen] = useState(true);

  useEffect(() => {
    let alive = true;
    window.api?.loadSettings?.().then((result) => {
      if (alive && result?.ok && !result.settings?.onboardingCompleted) setActiveSection("settings");
    }).catch(() => {});
    return () => { alive = false; };
  }, []);

  const inWorkspace = workspaceId !== null;
  const SectionView = SECTION_VIEWS[activeSection] ?? DashboardSection;
  const meta = inWorkspace
    ? { title: projectName ?? "Opening project…", context: "Editor" }
    : SECTION_META[activeSection] ?? SECTION_META.dashboard;

  const handleSelectSection = useCallback((id) => {
    if (id === "editor") {
      return;
    }
    setWorkspaceId(null);
    setProjectName(null);
    setActiveSection(id);
  }, []);

  const handleBackToProjects = useCallback(() => {
    setWorkspaceId(null);
    setProjectName(null);
  }, []);

  const handleOpenProject = useCallback((id) => {
    setProjectName(null);
    setWorkspaceId(id);
  }, []);

  const handleProjectLoaded = useCallback((project) => {
    setProjectName(project.name);
  }, []);

  return (
    <AppLayout
      sidebar={
        <Sidebar
          active={inWorkspace ? "editor" : activeSection}
          onSelect={handleSelectSection}
          collapsed={navigationCollapsed}
          inWorkspace={inWorkspace}
        />
      }
      header={
        <AppHeader
          title={meta.title}
          context={meta.context}
          navigationCollapsed={navigationCollapsed}
          onToggleNavigation={() => setNavigationCollapsed((value) => !value)}
          inspectorOpen={inspectorOpen}
          showInspectorToggle={inWorkspace}
          onToggleInspector={() => setInspectorOpen((value) => !value)}
        />
      }
    >
      <AnimatePresence mode="wait" initial={false}>
        <motion.div
          key={inWorkspace ? `workspace:${workspaceId}` : activeSection}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: MOTION.standard, ease: EASING.entrance }}
          className="h-full min-h-0 min-w-0"
        >
          {inWorkspace ? (
            <WorkspaceSection
              projectId={workspaceId}
              inspectorOpen={inspectorOpen}
              onProjectLoaded={handleProjectLoaded}
              onBack={handleBackToProjects}
            />
          ) : (
            <div className="h-full min-h-0 overflow-y-auto px-5 py-4">
              <SectionView
                {...(activeSection === "dashboard"
                  ? { onOpenProject: handleOpenProject }
                  : {})}
                {...(["prompts", "audio"].includes(activeSection)
                  ? { section: activeSection }
                  : {})}
              />
            </div>
          )}
        </motion.div>
      </AnimatePresence>
    </AppLayout>
  );
}
