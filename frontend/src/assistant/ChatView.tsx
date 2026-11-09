import { useQuery, useQueryClient } from "@tanstack/react-query";
import { SendHorizontal } from "lucide-react";
import { useEffect, useRef, useState, type KeyboardEvent, type SyntheticEvent } from "react";

import { ErrorState, LoadingState } from "@/components/states";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { describeError } from "@/lib/form-errors";
import { cn } from "@/lib/utils";

import {
  assistantKey,
  createThread,
  getThreadMessages,
  MESSAGE_MAX_LENGTH,
  resumeThread,
  sendMessage,
  threadMessagesKey,
  type AssistantEvent,
  type ChatMessage,
  type Citation,
  type PendingAction,
  type Thread,
} from "./assistant-api";
import { ConfirmationCard } from "./ConfirmationCard";
import { ToolSteps, type LiveStep } from "./ToolSteps";

interface LiveRun {
  /** The user's message, shown until the saved conversation is fetched again. */
  prompt: string | null;
  steps: LiveStep[];
  text: string;
  citations: Citation[];
  confirmation: PendingAction | null;
  error: string | null;
  running: boolean;
}

const IDLE: LiveRun = {
  prompt: null,
  steps: [],
  text: "",
  citations: [],
  confirmation: null,
  error: null,
  running: false,
};

function apply(run: LiveRun, event: AssistantEvent): LiveRun {
  switch (event.type) {
    case "tool_start":
      return { ...run, steps: [...run.steps, { name: event.name, label: event.label }] };
    case "tool_end": {
      const steps = [...run.steps];
      const index = steps.findLastIndex((s) => s.name === event.name && s.ok === undefined);
      if (index === -1) steps.push({ name: event.name, label: event.label, ok: event.ok });
      else steps[index] = { name: event.name, label: event.label, ok: event.ok };
      return { ...run, steps };
    }
    case "token":
      return { ...run, text: run.text + event.text };
    case "citation":
      return { ...run, citations: [...run.citations, event] };
    case "confirmation_required":
      return { ...run, confirmation: event.payload };
    case "done":
      return { ...run, text: event.content };
    case "error":
      return { ...run, error: event.message };
    case "status":
      return run;
  }
}

function Sources({ citations }: { citations: Citation[] }) {
  if (citations.length === 0) return null;
  return (
    <ol
      aria-label="Sources"
      className="mt-2 space-y-0.5 border-t pt-2 text-xs text-muted-foreground"
    >
      {citations.map((c) => (
        <li key={c.ref}>
          [{c.ref}] {c.title} › {c.heading}
        </li>
      ))}
    </ol>
  );
}

function Bubble({ from, children }: { from: "user" | "assistant"; children: React.ReactNode }) {
  return (
    <li
      aria-label={from === "user" ? "You" : "Assistant"}
      className={cn(
        "max-w-[min(42rem,90%)] rounded-lg px-3 py-2 text-sm",
        from === "user" ? "ml-auto bg-primary text-primary-foreground" : "bg-muted",
      )}
    >
      {children}
    </li>
  );
}

function SavedMessage({ message }: { message: ChatMessage }) {
  if (message.meta.kind === "decision") {
    return (
      <li className="text-center text-xs text-muted-foreground">
        {message.meta.decision === "confirm" ? "You confirmed." : "You cancelled."}
      </li>
    );
  }
  if (message.role === "user") {
    return (
      <Bubble from="user">
        <p className="whitespace-pre-wrap">{message.content}</p>
      </Bubble>
    );
  }
  const steps = message.meta.tools ?? [];
  if (!message.content && steps.length === 0) return null;
  return (
    <Bubble from="assistant">
      <ToolSteps steps={steps} />
      {message.content && (
        <p className={cn("whitespace-pre-wrap", steps.length > 0 && "mt-2")}>{message.content}</p>
      )}
      <Sources citations={message.meta.citations ?? []} />
    </Bubble>
  );
}

export function ChatView({
  threadId,
  onThreadCreated,
}: {
  threadId: string | null;
  onThreadCreated: (thread: Thread) => void;
}) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState("");
  const [live, setLive] = useState<LiveRun>(IDLE);
  const abort = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement | null>(null);

  const conversation = useQuery({
    queryKey: threadMessagesKey(threadId ?? "none"),
    queryFn: ({ signal }) => getThreadMessages(threadId ?? "", signal),
    // Not while a run streams: the saved copy of the user's message would show twice.
    enabled: threadId !== null && !live.running,
  });

  useEffect(() => () => abort.current?.abort(), []);
  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" });
  }, [conversation.data, live]);

  async function run(
    prompt: string | null,
    start: (id: string, signal: AbortSignal) => AsyncGenerator<AssistantEvent>,
  ) {
    abort.current?.abort();
    const controller = new AbortController();
    abort.current = controller;
    setLive({ ...IDLE, prompt, running: true });
    let error: string | null = null;
    try {
      let id = threadId;
      if (id === null) {
        const thread = await createThread();
        onThreadCreated(thread);
        id = thread.id;
      }
      for await (const event of start(id, controller.signal)) {
        if (event.type === "error") error = event.message;
        setLive((current) => apply(current, event));
      }
      await queryClient.invalidateQueries({ queryKey: assistantKey });
    } catch (caught) {
      if (controller.signal.aborted) return;
      error = describeError(caught);
      // Nothing was sent: give the text back.
      if (prompt !== null) setDraft(prompt);
      await queryClient.invalidateQueries({ queryKey: assistantKey });
    }
    setLive(error ? { ...IDLE, error } : IDLE);
  }

  function submit(event?: SyntheticEvent) {
    event?.preventDefault();
    const text = draft.trim();
    if (!text || live.running || text.length > MESSAGE_MAX_LENGTH) return;
    setDraft("");
    void run(text, (id, signal) => sendMessage(id, text, signal));
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      submit();
    }
  }

  function decide(decision: "confirm" | "cancel") {
    void run(null, (id, signal) => resumeThread(id, decision, signal));
  }

  const pending = live.running ? null : (conversation.data?.pending_action ?? null);
  const card = pending ?? live.confirmation;
  const messages = conversation.data?.messages ?? [];
  const showLiveAnswer =
    live.running && (live.steps.length > 0 || live.text !== "" || live.confirmation !== null);

  return (
    <div className="flex min-h-[60vh] flex-col gap-4">
      <div className="flex-1">
        {conversation.isLoading ? (
          <LoadingState label="Loading the conversation…" />
        ) : conversation.isError ? (
          <ErrorState
            message={describeError(conversation.error)}
            onRetry={() => void conversation.refetch()}
          />
        ) : messages.length === 0 && !live.running ? (
          <div className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground">
            <p className="font-medium text-foreground">Ask about policies or book GPUs.</p>
            <ul className="mt-2 list-disc space-y-1 pl-5">
              <li>How many GPU-hours do I get each week?</li>
              <li>I want to fine-tune a 7B model with LoRA. What should I book?</li>
              <li>Book 2 GPUs on an A100 server tomorrow from 19:00 to 23:00.</li>
            </ul>
          </div>
        ) : (
          <ul aria-label="Conversation" aria-live="polite" className="flex flex-col gap-3">
            {messages.map((message) => (
              <SavedMessage key={message.id} message={message} />
            ))}
            {live.running && live.prompt !== null && (
              <Bubble from="user">
                <p className="whitespace-pre-wrap">{live.prompt}</p>
              </Bubble>
            )}
            {showLiveAnswer && (
              <Bubble from="assistant">
                <ToolSteps steps={live.steps} />
                {live.text && (
                  <p className={cn("whitespace-pre-wrap", live.steps.length > 0 && "mt-2")}>
                    {live.text}
                  </p>
                )}
                <Sources citations={live.citations} />
              </Bubble>
            )}
            {live.running && !showLiveAnswer && (
              <li role="status" className="text-xs text-muted-foreground">
                Thinking…
              </li>
            )}
          </ul>
        )}
        {card && (
          <div className="mt-4">
            <ConfirmationCard action={card} busy={live.running} onDecide={decide} />
          </div>
        )}
        {live.error && (
          <Alert variant="destructive" className="mt-4">
            <AlertDescription>{live.error}</AlertDescription>
          </Alert>
        )}
        <div ref={bottom} />
      </div>

      <form onSubmit={submit} className="space-y-2">
        <Label htmlFor="assistant-message" className="sr-only">
          Message
        </Label>
        <Textarea
          id="assistant-message"
          value={draft}
          onChange={(e) => {
            setDraft(e.target.value);
          }}
          onKeyDown={onKeyDown}
          placeholder={pending ? "Or tell the assistant what to change…" : "Ask the assistant…"}
          maxLength={MESSAGE_MAX_LENGTH}
          rows={2}
          disabled={live.running}
          aria-describedby="assistant-message-hint"
        />
        <div className="flex items-center justify-between gap-2">
          <p id="assistant-message-hint" className="text-xs text-muted-foreground">
            {pending
              ? "Sending a message cancels the waiting confirmation."
              : "Enter to send, Shift+Enter for a new line."}{" "}
            {draft.length > MESSAGE_MAX_LENGTH - 200 &&
              `${String(draft.length)}/${String(MESSAGE_MAX_LENGTH)}`}
          </p>
          <Button type="submit" size="sm" disabled={live.running || draft.trim() === ""}>
            <SendHorizontal aria-hidden="true" />
            Send
          </Button>
        </div>
      </form>
    </div>
  );
}
