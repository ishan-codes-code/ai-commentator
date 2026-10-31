import { useCallback, useEffect, useState } from "react";
import { Film, ImageOff } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert.jsx";
import { Badge } from "@/components/ui/badge.jsx";
import { Button } from "@/components/ui/button.jsx";
import { Spinner } from "@/components/ui/spinner.jsx";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs.jsx";
import GlassCard from "@/primitives/GlassCard.jsx";
import { cn } from "cn";
import { projectMediaUrl } from "@/lib/format.js";

function RawJson({ data }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="flex flex-col gap-1.5">
      <Button
        type="button"
        variant="ghost"
        size="sm"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className="self-start"
      >
        {open ? "Hide raw JSON" : "Show raw JSON"}
      </Button>
      {open ? (
        <pre className="max-h-64 overflow-auto rounded-md border border-border bg-background p-3 font-mono text-[11px] leading-relaxed text-muted-foreground">
          {JSON.stringify(data, null, 2)}
        </pre>
      ) : null}
    </div>
  );
}

function FramesTab({ projectId, frames }) {
  const [selected, setSelected] = useState(null);
  const [broken, setBroken] = useState(() => new Set());
  useEffect(() => {
    setSelected(null);
    setBroken(new Set());
  }, [projectId]);
  if (frames.count === 0) {
    return (
      <p className="text-[13px] text-muted-foreground">
        No frames extracted yet. Run processing to extract frames from the
        source video.
      </p>
    );
  }
  const active = selected ?? frames.items[0];
  return (
    <div className="flex flex-col gap-3">
      {active ? (
        <figure className="flex flex-col gap-1.5">
          {broken.has(active.name) ? (
            <div className="flex aspect-video w-full flex-col items-center justify-center gap-1 rounded-card border border-border bg-background text-xs text-muted-foreground">
              <ImageOff className="size-5" aria-hidden />
              Could not load {active.name}
            </div>
          ) : (
            <img
              key={active.name}
              src={projectMediaUrl(projectId, "frames", active.name)}
              alt={`Extracted frame at ${active.timestamp}s`}
              onError={() =>
                setBroken((prev) => new Set(prev).add(active.name))
              }
              className="aspect-video w-full rounded-card border border-border bg-background object-contain"
            />
          )}
          <figcaption className="font-mono text-xs text-muted-foreground">
            {active.name} · t={active.timestamp}s
          </figcaption>
        </figure>
      ) : null}
      <div
        role="list"
        aria-label="Extracted frames"
        className="grid grid-cols-4 gap-2 sm:grid-cols-6"
      >
        {frames.items.map((frame) => (
          <button
            key={frame.name}
            type="button"
            role="listitem"
            aria-label={`Frame at ${frame.timestamp} seconds`}
            aria-pressed={active?.name === frame.name}
            onClick={() => setSelected(frame)}
            className="overflow-hidden rounded-md border border-border bg-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring aria-pressed:border-primary"
          >
            {broken.has(frame.name) ? (
              <span className="flex aspect-video items-center justify-center">
                <ImageOff className="size-4 text-subtle" aria-hidden />
              </span>
            ) : (
              <img
                src={projectMediaUrl(projectId, "frames", frame.name)}
                alt=""
                loading="lazy"
                onError={() =>
                  setBroken((prev) => new Set(prev).add(frame.name))
                }
                className="aspect-video w-full object-cover"
              />
            )}
          </button>
        ))}
      </div>
      {frames.count > frames.items.length ? (
        <p className="text-xs text-muted-foreground">
          Showing {frames.items.length} of {frames.count} frames.
        </p>
      ) : (
        <p className="font-mono text-xs text-muted-foreground">
          {frames.count} frames
        </p>
      )}
    </div>
  );
}

function ObservationsTab({ projectId, observations }) {
  const [selectedName, setSelectedName] = useState(null);
  const [doc, setDoc] = useState(null);
  const [docError, setDocError] = useState(null);
  useEffect(() => {
    setSelectedName(null);
    setDoc(null);
    setDocError(null);
  }, [projectId]);
  const load = useCallback(
    async (name) => {
      setSelectedName(name);
      setDoc(null);
      setDocError(null);
      try {
        const res = await window.api.readArtifact(projectId, "observation", name);
        if (!res || res.ok !== true) {
          throw new Error(res?.error ?? "Could not read the observation.");
        }
        setDoc(res.data);
      } catch (err) {
        setDocError(err?.message ?? "Could not read the observation.");
      }
    },
    [projectId],
  );
  if (observations.count === 0) {
    return (
      <p className="text-[13px] text-muted-foreground">
        No observations yet. Observations are written by vision analysis —
        skipped runs leave this empty by design.
      </p>
    );
  }
  const selectedDoc = doc && selectedName ? { name: selectedName, data: doc } : null;
  return (
    <div className="flex flex-col gap-2">
      <ul className="flex max-h-56 flex-col gap-1 overflow-y-auto">
        {observations.items.map((item) => (
          <li key={item.name}>
            <button
              type="button"
              aria-pressed={selectedName === item.name}
              onClick={() => load(item.name)}
              className="flex w-full items-center justify-between gap-2 rounded-md px-2.5 py-[7px] text-left text-[13px] text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring aria-pressed:bg-accent aria-pressed:text-foreground"
            >
              <span className="font-mono text-xs">t={item.timestamp}s</span>
              <span className="min-w-0 flex-1 truncate">{item.name}</span>
            </button>
          </li>
        ))}
      </ul>
      {observations.count > observations.items.length ? (
        <p className="text-xs text-muted-foreground">
          Showing {observations.items.length} of {observations.count}.
        </p>
      ) : null}
      {selectedName && docError ? (
        <Alert variant="destructive">
          <AlertTitle>Could not read {selectedName}</AlertTitle>
          <AlertDescription>{docError}</AlertDescription>
        </Alert>
      ) : null}
      {selectedDoc ? (
        <div className="flex flex-col gap-2 rounded-card border border-border bg-background p-4">
          <div className="flex items-center justify-between gap-2">
            <span className="font-mono text-xs text-foreground">
              {selectedDoc.name}
            </span>
            <Badge variant="outline">
              {selectedDoc.data.observations?.length ?? 0} claims
            </Badge>
          </div>
          {(selectedDoc.data.observations ?? []).map((claim, index) => (
            <div key={index} className="flex flex-col gap-0.5 border-t border-border pt-2">
              <div className="flex items-center gap-2">
                <Badge variant="secondary">{claim.category ?? "unknown"}</Badge>
                {claim.confidence ? (
                  <span className="text-xs text-muted-foreground">
                    {claim.confidence} confidence
                  </span>
                ) : null}
              </div>
              <p className="text-[13px] text-foreground">{claim.description}</p>
              {claim.evidence ? (
                <p className="text-xs text-muted-foreground">{claim.evidence}</p>
              ) : null}
            </div>
          ))}
          <RawJson data={selectedDoc.data} />
        </div>
      ) : null}
    </div>
  );
}

function StateTab({ state, readState }) {
  if (!state.present) {
    return (
      <p className="text-[13px] text-muted-foreground">
        No match state exists. It is rebuilt from observations during
        processing — extraction-only runs leave this empty by design.
      </p>
    );
  }
  if (state.invalid || !readState) {
    return (
      <Alert variant="destructive">
        <AlertTitle>Match state unreadable</AlertTitle>
        <AlertDescription>
          The stored match state failed validation. Re-running processing
          rebuilds it from observations.
        </AlertDescription>
      </Alert>
    );
  }
  const facts = Array.isArray(readState.facts) ? readState.facts : [];
  return (
    <div className="flex flex-col gap-2">
      <dl className="flex flex-col gap-1.5">
        <div className="flex items-baseline justify-between gap-4">
          <dt className="text-[13px] text-muted-foreground">Schema</dt>
          <dd className="font-mono text-[13px] text-foreground">
            {readState.schema_version ?? "—"}
          </dd>
        </div>
        <div className="flex items-baseline justify-between gap-4">
          <dt className="text-[13px] text-muted-foreground">Latest timestamp</dt>
          <dd className="font-mono text-[13px] text-foreground">
            {readState.latest_timestamp ?? "—"}s
          </dd>
        </div>
        <div className="flex items-baseline justify-between gap-4">
          <dt className="text-[13px] text-muted-foreground">Facts</dt>
          <dd className="font-mono text-[13px] text-foreground">{facts.length}</dd>
        </div>
        <div className="flex items-baseline justify-between gap-4">
          <dt className="text-[13px] text-muted-foreground">Uncertainties</dt>
          <dd className="font-mono text-[13px] text-foreground">
            {Array.isArray(readState.uncertainties) ? readState.uncertainties.length : 0}
          </dd>
        </div>
      </dl>
      <RawJson data={readState} />
    </div>
  );
}

function EventsTab({ events, readEvents }) {
  if (!events.present) {
    return (
      <p className="text-[13px] text-muted-foreground">
        No events detected yet. Event detection runs on a validated match
        state during processing.
      </p>
    );
  }
  if (events.invalid || !readEvents) {
    return (
      <Alert variant="destructive">
        <AlertTitle>Events unreadable</AlertTitle>
        <AlertDescription>
          The stored events file failed validation.
        </AlertDescription>
      </Alert>
    );
  }
  const list = Array.isArray(readEvents.events) ? readEvents.events : [];
  if (list.length === 0) {
    return (
      <p className="text-[13px] text-muted-foreground">
        Detection ran and confirmed zero events. That is a real result,
        not missing data.
      </p>
    );
  }
  return (
    <ul className="flex flex-col gap-2">
      {list.map((event, index) => (
        <li
          key={event.event_id ?? index}
          className="flex flex-col gap-0.5 rounded-card border border-border bg-background p-3"
        >
          <div className="flex items-center gap-2">
            <Badge variant="secondary">{event.type ?? "event"}</Badge>
            {typeof event.timestamp !== "undefined" ? (
              <span className="font-mono text-xs text-muted-foreground">
                t={event.timestamp}s
              </span>
            ) : null}
          </div>
          {event.description ? (
            <p className="text-[13px] text-foreground">{event.description}</p>
          ) : null}
          {event.evidence ? (
            <p className="text-xs text-muted-foreground">{event.evidence}</p>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

function CommentaryTab({ commentary, readCommentary }) {
  if (!commentary.present) {
    return (
      <p className="text-[13px] text-muted-foreground">
        No commentary generated yet. Commentary is written from detected
        events during processing — never invented.
      </p>
    );
  }
  if (commentary.invalid || !readCommentary) {
    return (
      <Alert variant="destructive">
        <AlertTitle>Commentary unreadable</AlertTitle>
        <AlertDescription>
          The stored commentary file failed validation.
        </AlertDescription>
      </Alert>
    );
  }
  const lines = Array.isArray(readCommentary.lines) ? readCommentary.lines : [];
  if (lines.length === 0) {
    return (
      <p className="text-[13px] text-muted-foreground">
        Generation ran and deliberately wrote zero lines (repeat
        suppression). That is a real result, not missing data.
      </p>
    );
  }
  return (
    <ol className="flex flex-col gap-2">
      {lines.map((line, index) => (
        <li
          key={line.line_id ?? index}
          className="flex flex-col gap-0.5 rounded-card border border-border bg-background p-3"
        >
          <div className="flex items-center gap-2">
            {typeof line.timestamp !== "undefined" ? (
              <span className="font-mono text-xs text-secondary">
                t={line.timestamp}s
              </span>
            ) : null}
            {line.event_id ? (
              <span className="font-mono text-xs text-muted-foreground">
                {line.event_id}
              </span>
            ) : null}
          </div>
          <p className="text-[13px] text-foreground">{line.text}</p>
        </li>
      ))}
    </ol>
  );
}

/**
 * Project-scoped artifact browser. Everything shown comes from
 * main-process listing/reads of the project's own output tree; absent
 * artifacts render honest empty states, never placeholders.
 */
export default function ArtifactBrowser({ projectId, refreshSignal, className }) {
  const [listing, setListing] = useState(null);
  const [docs, setDocs] = useState({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [retryNonce, setRetryNonce] = useState(0);

  useEffect(() => {
    let alive = true;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        if (typeof window.api?.listArtifacts !== "function") {
          throw new Error("Worker bridge unavailable (running outside Electron?).");
        }
        const res = await window.api.listArtifacts(projectId);
        if (!res || res.ok !== true || !res.artifacts) {
          throw new Error(res?.error ?? "Could not list artifacts.");
        }
        if (!alive) {
          return;
        }
        setListing(res.artifacts);
        const loaded = {};
        for (const kind of ["state", "events", "commentary"]) {
          const summary = res.artifacts[kind];
          if (summary?.present && !summary?.invalid) {
            try {
              const doc = await window.api.readArtifact(projectId, kind);
              if (doc?.ok === true) {
                loaded[kind] = doc.data;
              }
            } catch {
              // Keep the summary; the tab explains the gap.
            }
          }
        }
        if (alive) {
          setDocs(loaded);
          setLoading(false);
        }
      } catch (err) {
        if (alive) {
          setError(err?.message ?? "Could not list artifacts.");
          setLoading(false);
        }
      }
    }
    load();
    return () => {
      alive = false;
    };
  }, [projectId, refreshSignal, retryNonce]);

  if (loading && !listing) {
    return (
      <GlassCard className={cn("flex items-center gap-2 p-5 text-[13px] text-muted-foreground", className)}>
        <Spinner />
        Loading artifacts…
      </GlassCard>
    );
  }

  if (error || !listing) {
    return (
      <div className={cn("flex flex-col gap-2", className)}>
        <Alert variant="destructive">
          <AlertTitle>Could not load artifacts</AlertTitle>
          <AlertDescription>{error ?? "Unknown error."}</AlertDescription>
        </Alert>
        <Button
          type="button"
          variant="outline"
          onClick={() => setRetryNonce((value) => value + 1)}
        >
          Retry
        </Button>
      </div>
    );
  }

  return (
    <GlassCard className={cn("flex min-h-full flex-col gap-3 p-4", className)}>
      <div className="flex items-center gap-2">
        <Film className="size-4 text-muted-foreground" aria-hidden />
        <h2 className="text-[13px] font-semibold tracking-[-0.01em] text-foreground">
          Artifacts
        </h2>
      </div>
      <Tabs defaultValue="frames" className="min-h-0 flex-1">
        <TabsList aria-label="Artifact categories" className="w-full shrink-0 overflow-x-auto">
          <TabsTrigger value="frames">
            Frames{listing.frames.count > 0 ? ` ${listing.frames.count}` : ""}
          </TabsTrigger>
          <TabsTrigger value="observations">
            Observations{listing.observations.count > 0 ? ` ${listing.observations.count}` : ""}
          </TabsTrigger>
          <TabsTrigger value="state">Match state</TabsTrigger>
          <TabsTrigger value="events">
            Events{listing.events.count > 0 ? ` ${listing.events.count}` : ""}
          </TabsTrigger>
          <TabsTrigger value="commentary">
            Commentary{listing.commentary.lines > 0 ? ` ${listing.commentary.lines}` : ""}
          </TabsTrigger>
        </TabsList>
        <TabsContent value="frames" className="min-h-0 flex-1 overflow-y-auto">
          <FramesTab projectId={projectId} frames={listing.frames} />
        </TabsContent>
        <TabsContent value="observations" className="min-h-0 flex-1 overflow-y-auto">
          <ObservationsTab projectId={projectId} observations={listing.observations} />
        </TabsContent>
        <TabsContent value="state" className="min-h-0 flex-1 overflow-y-auto">
          <StateTab state={listing.state} readState={docs.state} />
        </TabsContent>
        <TabsContent value="events" className="min-h-0 flex-1 overflow-y-auto">
          <EventsTab events={listing.events} readEvents={docs.events} />
        </TabsContent>
        <TabsContent value="commentary" className="min-h-0 flex-1 overflow-y-auto">
          <CommentaryTab commentary={listing.commentary} readCommentary={docs.commentary} />
        </TabsContent>
      </Tabs>
    </GlassCard>
  );
}
