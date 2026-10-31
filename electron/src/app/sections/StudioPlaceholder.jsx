import { AudioLines, Sparkles } from "lucide-react";
import GlassCard from "@/primitives/GlassCard.jsx";

const CONTENT = {
  prompts: {
    title: "Prompt inspection is not available yet",
    description: "AI prompt viewing and editing are planned for a later studio milestone.",
    icon: Sparkles,
  },
  audio: {
    title: "Audio playback is not available yet",
    description: "The tray shows the planned playback lanes. Audio generation and synchronized playback have not been implemented.",
    icon: AudioLines,
  },
};

export default function StudioPlaceholder({ section }) {
  const content = CONTENT[section] ?? CONTENT.prompts;
  const Icon = content.icon;
  return (
    <section aria-labelledby="placeholder-title" className="flex h-full min-h-0 items-center justify-center">
      <GlassCard className="flex max-w-md flex-col items-center gap-3 p-8 text-center">
        <span className="flex size-9 items-center justify-center rounded-sm border border-border bg-muted text-muted-foreground">
          <Icon className="size-4" aria-hidden />
        </span>
        <h2 id="placeholder-title" className="text-[14px] font-medium text-foreground">{content.title}</h2>
        <p className="text-[12px] text-muted-foreground">{content.description}</p>
      </GlassCard>
    </section>
  );
}
