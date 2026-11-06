import { ImageOff, Loader2, Upload, X } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { ApiError } from "@/lib/api";
import type { UploadPurpose } from "@/lib/api-types";
import type { ImageChoice } from "@/lib/image-choice";
import { describeError } from "@/lib/form-errors";
import { checkImageFile, IMAGE_ACCEPT, UploadError, uploadImage } from "@/lib/uploads";
import { cn } from "@/lib/utils";

function uploadErrorMessage(error: unknown): string {
  // Storage answers in XML; UploadError carries a readable sentence instead.
  if (error instanceof UploadError) return error.message;
  if (error instanceof ApiError) return error.message;
  return describeError(error);
}

/**
 * Pick an image and upload it straight to storage. The parent saves the resulting key
 * with the rest of its form; until then the upload is only a candidate.
 */
export function ImageUploadField({
  label,
  purpose,
  currentUrl,
  alt,
  choice,
  onChoiceChange,
  onUploadingChange,
  shape = "wide",
  className,
}: {
  label: string;
  purpose: UploadPurpose;
  /** The saved image, if any. */
  currentUrl: string | null;
  alt: string;
  choice: ImageChoice;
  onChoiceChange: (choice: ImageChoice) => void;
  onUploadingChange: (uploading: boolean) => void;
  /** "wide" for photos of hardware, "round" for profile photos. */
  shape?: "wide" | "round";
  className?: string;
}) {
  const inputId = useId();
  const errorId = `${inputId}-error`;
  const inputRef = useRef<HTMLInputElement>(null);
  // Bumped per file chosen, so a slow upload that was superseded is ignored.
  const attempt = useRef(0);
  // Every object URL made here, so none outlives the field.
  const objectUrls = useRef(new Set<string>());
  // The uploaded image the choice points at, and the one still uploading.
  const [preview, setPreview] = useState<string | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const urls = objectUrls.current;
    return () => {
      for (const url of urls) URL.revokeObjectURL(url);
      urls.clear();
    };
  }, []);

  const release = (url: string | null) => {
    if (url === null) return;
    URL.revokeObjectURL(url);
    objectUrls.current.delete(url);
  };

  const setBusy = (busy: boolean) => {
    setUploading(busy);
    onUploadingChange(busy);
  };

  const choose = async (file: File) => {
    const id = ++attempt.current;
    // Checked before asking for a signed form: no request for a file the API would refuse.
    const problem = checkImageFile(file, purpose);
    if (problem) {
      setError(problem);
      return;
    }
    const url = URL.createObjectURL(file);
    objectUrls.current.add(url);
    release(pending);
    setPending(url);
    setError(null);
    setBusy(true);
    try {
      const key = await uploadImage(file, purpose);
      if (id !== attempt.current) {
        release(url);
        return;
      }
      release(preview);
      setPreview(url);
      setPending(null);
      onChoiceChange({ kind: "set", key });
    } catch (caught) {
      release(url);
      if (id !== attempt.current) return;
      // The previous choice and its preview stay as they were.
      setPending(null);
      setError(uploadErrorMessage(caught));
    } finally {
      if (id === attempt.current) setBusy(false);
    }
  };

  const remove = () => {
    attempt.current += 1;
    release(pending);
    release(preview);
    setPending(null);
    setPreview(null);
    setError(null);
    setBusy(false);
    onChoiceChange({ kind: "remove" });
  };

  const saved = choice.kind === "set" ? preview : choice.kind === "keep" ? currentUrl : null;
  const shown = pending ?? saved;

  return (
    <div className={cn("grid gap-2", className)}>
      <Label htmlFor={inputId}>{label}</Label>
      <div className="flex flex-wrap items-center gap-3">
        <div
          className={cn(
            "relative flex items-center justify-center overflow-hidden border bg-muted",
            shape === "round" ? "size-20 rounded-full" : "aspect-[2/1] w-40 rounded-md",
          )}
        >
          {shown ? (
            <img src={shown} alt={alt} className="size-full object-cover" />
          ) : (
            <ImageOff className="size-6 text-muted-foreground" aria-hidden="true" />
          )}
          {uploading && (
            <div className="absolute inset-0 flex items-center justify-center bg-background/60">
              <Loader2 className="size-5 animate-spin" aria-hidden="true" />
            </div>
          )}
        </div>
        <div className="flex flex-wrap gap-2">
          <input
            ref={inputRef}
            id={inputId}
            type="file"
            accept={IMAGE_ACCEPT}
            className="sr-only"
            aria-describedby={error ? errorId : undefined}
            onChange={(event) => {
              const file = event.target.files?.[0];
              // Cleared so choosing the same file again still fires a change.
              event.target.value = "";
              if (file) void choose(file);
            }}
          />
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => inputRef.current?.click()}
          >
            <Upload aria-hidden="true" />
            {shown ? "Replace image" : "Choose image"}
          </Button>
          {(shown !== null || uploading) && (
            <Button type="button" variant="ghost" size="sm" onClick={remove}>
              <X aria-hidden="true" />
              Remove image
            </Button>
          )}
        </div>
      </div>
      <p role="status" className="text-xs text-muted-foreground">
        {uploading ? "Uploading…" : "PNG, JPEG, or WebP."}
      </p>
      {error && (
        <p id={errorId} role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
    </div>
  );
}
