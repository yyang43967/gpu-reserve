import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { MessageSquarePlus } from "lucide-react";

import { ChatView } from "@/assistant/ChatView";
import { listThreads, threadsKey, type Thread } from "@/assistant/assistant-api";
import { PageHeader } from "@/components/page-header";
import { ErrorState, LoadingState } from "@/components/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { describeError } from "@/lib/form-errors";
import { useLatestSearchParams } from "@/lib/use-search-params";
import { cn } from "@/lib/utils";

export function AssistantPage() {
  const [searchParams, updateSearchParams] = useLatestSearchParams();
  const queryClient = useQueryClient();
  const threadId = searchParams.get("thread");
  // Bumped when the person picks a conversation, so the chat starts fresh. A thread
  // created by the chat's own first message keeps the chat mounted: its answer is
  // still streaming in.
  const [chatKey, setChatKey] = useState(0);
  const threads = useQuery({
    queryKey: threadsKey,
    queryFn: ({ signal }) => listThreads(signal),
  });

  function select(id: string | null) {
    updateSearchParams((params) => {
      if (id === null) params.delete("thread");
      else params.set("thread", id);
      return params;
    });
  }

  function open(id: string | null) {
    setChatKey((key) => key + 1);
    select(id);
  }

  function created(thread: Thread) {
    void queryClient.invalidateQueries({ queryKey: threadsKey });
    select(thread.id);
  }

  return (
    <>
      <PageHeader
        title="Assistant"
        description="Ask about policies, find hardware for a job, and book or cancel with your confirmation."
        actions={
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              open(null);
            }}
          >
            <MessageSquarePlus aria-hidden="true" />
            New conversation
          </Button>
        }
      />
      <div className="grid gap-6 md:grid-cols-[15rem_1fr]">
        <nav aria-label="Conversations" className="min-w-0">
          {threads.isPending ? (
            <LoadingState label="Loading conversations…" />
          ) : threads.isError ? (
            <ErrorState
              message={describeError(threads.error)}
              onRetry={() => void threads.refetch()}
            />
          ) : threads.data.items.length === 0 ? (
            <p className="text-sm text-muted-foreground">No conversations yet.</p>
          ) : (
            <ul className="flex gap-2 overflow-x-auto md:flex-col md:overflow-visible">
              {threads.data.items.map((thread) => (
                <li key={thread.id} className="shrink-0 md:shrink">
                  <button
                    type="button"
                    onClick={() => {
                      open(thread.id);
                    }}
                    aria-current={thread.id === threadId ? "page" : undefined}
                    className={cn(
                      "w-full max-w-60 truncate rounded-md px-3 py-2 text-left text-sm hover:bg-muted md:max-w-none",
                      thread.id === threadId && "bg-muted font-medium",
                    )}
                  >
                    {thread.title}
                    {thread.has_pending_action && (
                      <Badge variant="secondary" className="ml-2">
                        Waiting
                      </Badge>
                    )}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </nav>
        <section aria-label="Chat" className="min-w-0">
          <ChatView key={chatKey} threadId={threadId} onThreadCreated={created} />
        </section>
      </div>
    </>
  );
}
