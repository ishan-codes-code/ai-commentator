import { useCallback, useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import {
  Clapperboard,
  FolderOpen,
  Plus,
  Trash2,
} from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert.jsx";
import { Badge } from "@/components/ui/badge.jsx";
import { Button } from "@/components/ui/button.jsx";
import { Spinner } from "@/components/ui/spinner.jsx";
import GlassCard from "@/primitives/GlassCard.jsx";
import WorkerStatus from "@/components/WorkerStatus.jsx";
import {
  extensionOf,
  fileNameOf,
  formatBytes,
  timeAgo,
} from "@/lib/format.js";
import { EASING, MOTION } from "@/lib/tokens.js";

const STATUS_BADGE = {
  idle: "outline",
  processing: "default",
  done: "secondary",
  error: "destructive",
  cancelled: "outline",
  interrupted: "outline",
};

function bridgeError() {
  return new Error("Worker bridge unavailable (running outside Electron?).");
}

function ProjectRow({ project, onOpen, onDelete, deleting, onArmDelete, armed }) {
  return (
    <li className="flex items-center gap-3 px-4 py-3">
      <button
        type="button"
        onClick={() => onOpen(project.id)}
        className="flex min-w-0 flex-1 items-center gap-3 rounded-md text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <span className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span className="truncate text-[13px] font-semibold text-foreground">
            {project.name}
          </span>
          <span className="truncate font-mono text-xs text-subtle">
            {fileNameOf(project.sourceVideo.path)} ·{" "}
            {formatBytes(project.sourceVideo.sizeBytes)} · opened{" "}
            {timeAgo(project.lastOpenedAt)}
          </span>
        </span>
        <Badge variant={STATUS_BADGE[project.processingStatus] ?? "outline"}>
          {project.processingStatus}
        </Badge>
      </button>
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        aria-label={armed ? `Confirm delete ${project.name}` : `Delete ${project.name}`}
        disabled={deleting}
        onClick={() => (armed ? onDelete(project.id) : onArmDelete(project.id))}
        className={armed ? "text-destructive hover:text-destructive" : undefined}
      >
        {deleting ? <Spinner /> : <Trash2 />}
      </Button>
    </li>
  );
}

/**
 * Project-first dashboard. Lists locally stored projects from Electron
 * userData (via audited IPC — the renderer never touches the
 * filesystem). Creating a project picks a video with the native dialog,
 * names it, copies it into an isolated project directory, and opens the
 * workspace. No fake projects, no statistics.
 */
export default function DashboardSection({ onOpenProject }) {
  const [projects, setProjects] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [dialogBusy, setDialogBusy] = useState(false);
  const [pendingPath, setPendingPath] = useState(null);
  const [name, setName] = useState("");
  const [creating, setCreating] = useState(false);
  const [flowError, setFlowError] = useState(null);
  const [armedDelete, setArmedDelete] = useState(null);
  const [deletingId, setDeletingId] = useState(null);

  const refresh = useCallback(async () => {
    if (typeof window.api?.listProjects !== "function") {
      throw bridgeError();
    }
    const res = await window.api.listProjects();
    if (!res || res.ok !== true) {
      throw new Error(res?.error ?? "Could not load projects.");
    }
    return res.projects ?? [];
  }, []);

  useEffect(() => {
    let alive = true;
    refresh()
      .then((list) => {
        if (alive) {
          setProjects(list);
          setLoading(false);
        }
      })
      .catch((err) => {
        if (alive) {
          setError(err?.message ?? "Could not load projects.");
          setLoading(false);
        }
      });
    return () => {
      alive = false;
    };
  }, [refresh]);

  async function handleAdd() {
    if (dialogBusy || creating) {
      return;
    }
    setDialogBusy(true);
    setFlowError(null);
    try {
      if (typeof window.api?.selectVideo !== "function") {
        throw bridgeError();
      }
      const res = await window.api.selectVideo();
      if (!res) {
        throw new Error("No response from the file dialog.");
      }
      if (res.cancelled) {
        return;
      }
      if (res.ok !== true || !res.path) {
        throw new Error(res.error ?? "Could not select a video.");
      }
      setPendingPath(res.path);
      setName(fileNameOf(res.path).replace(/\.[^.]+$/, ""));
    } catch (err) {
      setFlowError(err?.message ?? "Could not select a video.");
    } finally {
      setDialogBusy(false);
    }
  }

  async function handleCreate() {
    if (creating || !pendingPath) {
      return;
    }
    setCreating(true);
    setFlowError(null);
    try {
      if (typeof window.api?.createProject !== "function") {
        throw bridgeError();
      }
      const res = await window.api.createProject(name, pendingPath);
      if (!res || res.ok !== true || !res.project) {
        throw new Error(res?.error ?? "Could not create the project.");
      }
      setPendingPath(null);
      setName("");
      setProjects(await refresh());
      onOpenProject(res.project.id);
    } catch (err) {
      setFlowError(err?.message ?? "Could not create the project.");
    } finally {
      setCreating(false);
    }
  }

  async function handleDelete(id) {
    setDeletingId(id);
    setArmedDelete(null);
    try {
      if (typeof window.api?.deleteProject !== "function") {
        throw bridgeError();
      }
      const res = await window.api.deleteProject(id);
      if (!res || res.ok !== true) {
        throw new Error(res?.error ?? "Could not delete the project.");
      }
      setProjects(await refresh());
    } catch (err) {
      setFlowError(err?.message ?? "Could not delete the project.");
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-2">
        <p className="text-[13px] text-muted-foreground">
          {loading
            ? "Loading projects…"
            : `${projects.length} ${projects.length === 1 ? "project" : "projects"}`}
        </p>
        <Button
          type="button"
          variant="default"
          disabled={dialogBusy || creating || pendingPath !== null}
          onClick={handleAdd}
        >
          {dialogBusy ? (
            <>
              <Spinner data-icon="inline-start" />
              Opening…
            </>
          ) : (
            <>
              <Plus data-icon="inline-start" />
              Add Project
            </>
          )}
        </Button>
      </div>

      <AnimatePresence mode="wait" initial={false}>
        <motion.div
          key={pendingPath ? "naming" : "list"}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: MOTION.standard, ease: EASING.entrance }}
        >
          {pendingPath ? (
            <GlassCard className="flex flex-col gap-3 p-5">
              <div className="flex flex-col gap-0.5">
                <h2 className="text-[13px] font-semibold tracking-[-0.01em] text-foreground">
                  Name your project
                </h2>
                <p className="truncate font-mono text-xs text-subtle">
                  {pendingPath} · .{extensionOf(pendingPath)}
                </p>
              </div>
              <label
                htmlFor="project-name"
                className="text-xs font-medium text-muted-foreground"
              >
                Project name
              </label>
              <input
                id="project-name"
                type="text"
                value={name}
                maxLength={100}
                onChange={(event) => setName(event.target.value)}
                placeholder="Friday finals"
                className="rounded-md border border-input bg-background px-3 py-2 text-[13px] text-foreground placeholder:text-subtle focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
              <div className="flex flex-wrap gap-2">
                <Button
                  type="button"
                  variant="default"
                  disabled={creating || name.trim() === ""}
                  onClick={handleCreate}
                >
                  {creating ? (
                    <>
                      <Spinner data-icon="inline-start" />
                      Importing…
                    </>
                  ) : (
                    <>
                      <FolderOpen data-icon="inline-start" />
                      Create project
                    </>
                  )}
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  disabled={creating}
                  onClick={() => {
                    setPendingPath(null);
                    setName("");
                    setFlowError(null);
                  }}
                >
                  Cancel
                </Button>
              </div>
              <p className="text-xs text-muted-foreground">
                The video is copied into an isolated project folder. The
                original file is left untouched.
              </p>
            </GlassCard>
          ) : loading ? (
            <GlassCard className="flex items-center gap-2 p-5 text-[13px] text-muted-foreground">
              <Spinner />
              Loading projects…
            </GlassCard>
          ) : error ? (
            <Alert variant="destructive">
              <AlertTitle>Could not load projects</AlertTitle>
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : projects.length === 0 ? (
            <GlassCard className="flex flex-col items-center gap-3 p-6 text-center">
              <span className="flex size-10 items-center justify-center rounded-lg bg-accent text-muted-foreground">
                <Clapperboard className="size-5" aria-hidden />
              </span>
              <h2 className="text-[15px] font-semibold tracking-[-0.01em] text-foreground">
                No projects yet
              </h2>
              <p className="max-w-sm text-[13px] text-muted-foreground">
                Create a project from a recorded match video. Each project
                keeps its own source copy and processing output.
              </p>
              <Button type="button" variant="default" onClick={handleAdd}>
                <Plus data-icon="inline-start" />
                Add Project
              </Button>
            </GlassCard>
          ) : (
            <GlassCard className="p-0">
              <ul className="flex flex-col divide-y divide-border">
                {projects.map((project) => (
                  <ProjectRow
                    key={project.id}
                    project={project}
                    onOpen={onOpenProject}
                    onDelete={handleDelete}
                    deleting={deletingId === project.id}
                    armed={armedDelete === project.id}
                    onArmDelete={setArmedDelete}
                  />
                ))}
              </ul>
            </GlassCard>
          )}
        </motion.div>
      </AnimatePresence>

      {flowError ? (
        <Alert variant="destructive">
          <AlertTitle>Something went wrong</AlertTitle>
          <AlertDescription>{flowError}</AlertDescription>
        </Alert>
      ) : null}

      <WorkerStatus />
    </div>
  );
}
