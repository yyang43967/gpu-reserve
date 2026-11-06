import { Button } from "@/components/ui/button";
import type { Page } from "@/lib/api-types";
import { pageCount } from "@/lib/pagination";

export function Pagination({
  data,
  onPage,
}: {
  data: Page<unknown>;
  onPage: (page: number) => void;
}) {
  const totalPages = pageCount(data);
  const first = (data.page - 1) * data.page_size + 1;
  const last = first + data.items.length - 1;

  return (
    <nav aria-label="Pagination" className="mt-4 flex flex-wrap items-center justify-between gap-3">
      <p className="text-sm text-muted-foreground">
        {first}–{last} of {data.total}
      </p>
      <div className="flex items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          disabled={data.page <= 1}
          onClick={() => {
            onPage(data.page - 1);
          }}
        >
          Previous
        </Button>
        <span className="text-sm tabular-nums">
          Page {data.page} of {totalPages}
        </span>
        <Button
          variant="outline"
          size="sm"
          disabled={data.page >= totalPages}
          onClick={() => {
            onPage(data.page + 1);
          }}
        >
          Next
        </Button>
      </div>
    </nav>
  );
}
