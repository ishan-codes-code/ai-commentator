import { motion } from "motion/react";
import DockedWorkspace from "./DockedWorkspace.jsx";

export default function ProjectWorkspaceLayout({ main, inspector, inspectorOpen }) {
  return (
    <div className="grid h-full min-h-0 min-w-0 grid-rows-[minmax(0,1fr)_196px] overflow-hidden">
      <div
        className="grid min-h-0 min-w-0 overflow-hidden transition-[grid-template-columns] duration-150 motion-reduce:transition-none"
        style={{
          gridTemplateColumns: inspectorOpen
            ? "minmax(0, 1fr) 320px"
            : "minmax(0, 1fr) 0px",
        }}
      >
        <main className="min-h-0 min-w-0 overflow-hidden">{main}</main>
        <motion.aside
          id="project-inspector"
          aria-label="Project inspector"
          aria-hidden={!inspectorOpen}
          inert={!inspectorOpen}
          initial={false}
          animate={inspectorOpen ? { opacity: 1, x: 0 } : { opacity: 0, x: 8 }}
          transition={{ duration: 0.15, ease: "easeOut" }}
          className={`h-full min-h-0 w-[320px] overflow-hidden ${inspectorOpen ? "border-l border-border" : "pointer-events-none"}`}
        >
          {inspector}
        </motion.aside>
      </div>
      <DockedWorkspace />
    </div>
  );
}
