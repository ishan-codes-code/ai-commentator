import { useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import AppLayout from "@/layout/AppLayout.jsx";
import AppHeader from "@/layout/AppHeader.jsx";
import Sidebar from "@/layout/Sidebar.jsx";
import DashboardSection from "@/app/sections/DashboardSection.jsx";
import MatchesSection from "@/app/sections/MatchesSection.jsx";
import CommentarySection from "@/app/sections/CommentarySection.jsx";
import SettingsSection from "@/app/sections/SettingsSection.jsx";
import WorkspaceSection from "@/app/sections/WorkspaceSection.jsx";
import { MOTION, EASING } from "@/lib/tokens.js";

const SECTION_VIEWS = {
  dashboard: DashboardSection,
  matches: MatchesSection,
  commentary: CommentarySection,
  settings: SettingsSection,
};

const SECTION_META = {
  dashboard: { title: "Your Projects", context: "Local project library" },
  matches: { title: "Matches", context: "Recorded matches" },
  commentary: { title: "Commentary", context: "Generated lines and scripts" },
  settings: { title: "Settings", context: "Application preferences" },
};

export default function App() {
  const [activeSection, setActiveSection] = useState("dashboard");
  const [workspaceId, setWorkspaceId] = useState(null);

  const inWorkspace = workspaceId !== null;
  const SectionView = SECTION_VIEWS[activeSection] ?? DashboardSection;
  const meta = inWorkspace
    ? { title: "Project workspace", context: "Isolated run environment" }
    : (SECTION_META[activeSection] ?? SECTION_META.dashboard);

  function handleSelectSection(id) {
    setWorkspaceId(null);
    setActiveSection(id);
  }

  return (
    <AppLayout
      sidebar={<Sidebar active={inWorkspace ? "" : activeSection} onSelect={handleSelectSection} />}
      header={<AppHeader title={meta.title} context={meta.context} />}
    >
      <AnimatePresence mode="wait" initial={false}>
        <motion.div
          key={inWorkspace ? `workspace:${workspaceId}` : activeSection}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: MOTION.standard, ease: EASING.entrance }}
        >
          {inWorkspace ? (
            <WorkspaceSection
              projectId={workspaceId}
              onBack={() => setWorkspaceId(null)}
            />
          ) : (
            <SectionView
              {...(activeSection === "dashboard"
                ? { onOpenProject: setWorkspaceId }
                : {})}
            />
          )}
        </motion.div>
      </AnimatePresence>
    </AppLayout>
  );
}
