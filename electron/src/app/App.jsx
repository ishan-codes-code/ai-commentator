import { useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import AppLayout from "@/layout/AppLayout.jsx";
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

export default function App() {
  const [activeSection, setActiveSection] = useState("dashboard");
  const SectionView = SECTION_VIEWS[activeSection] ?? DashboardSection;

  return (
    <AppLayout sidebar={<Sidebar active={activeSection} onSelect={setActiveSection} />}>
      <AnimatePresence mode="wait" initial={false}>
        <motion.div
          key={activeSection}
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -8 }}
          transition={{ duration: MOTION.page, ease: EASING.entrance }}
        >
          <SectionView />
        </motion.div>
      </AnimatePresence>
    </AppLayout>
  );
}
