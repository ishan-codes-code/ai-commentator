import { useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import AppLayout from "@/layout/AppLayout.jsx";
import AppHeader from "@/layout/AppHeader.jsx";
import Sidebar from "@/layout/Sidebar.jsx";
import DashboardSection from "@/app/sections/DashboardSection.jsx";
import MatchesSection from "@/app/sections/MatchesSection.jsx";
import CommentarySection from "@/app/sections/CommentarySection.jsx";
import SettingsSection from "@/app/sections/SettingsSection.jsx";
import { MOTION, EASING } from "@/lib/tokens.js";

const SECTION_VIEWS = {
  dashboard: DashboardSection,
  matches: MatchesSection,
  commentary: CommentarySection,
  settings: SettingsSection,
};

const SECTION_META = {
  dashboard: { title: "Dashboard", context: "Import and processing runs" },
  matches: { title: "Matches", context: "Recorded matches" },
  commentary: { title: "Commentary", context: "Generated lines and scripts" },
  settings: { title: "Settings", context: "Application preferences" },
};

export default function App() {
  const [activeSection, setActiveSection] = useState("dashboard");
  const SectionView = SECTION_VIEWS[activeSection] ?? DashboardSection;
  const meta = SECTION_META[activeSection] ?? SECTION_META.dashboard;

  return (
    <AppLayout
      sidebar={<Sidebar active={activeSection} onSelect={setActiveSection} />}
      header={<AppHeader title={meta.title} context={meta.context} />}
    >
      <AnimatePresence mode="wait" initial={false}>
        <motion.div
          key={activeSection}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: MOTION.standard, ease: EASING.entrance }}
        >
          <SectionView />
        </motion.div>
      </AnimatePresence>
    </AppLayout>
  );
}
