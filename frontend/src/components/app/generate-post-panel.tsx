"use client";

import { useState, useEffect, useCallback } from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import { Check, Copy, Loader2, Sparkles, Save } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { useCopyText } from "@/lib/copy-text";

interface GeneratePostPanelProps {
  taskId: string;
  clipId: string;
  /** Called after hook_title is persisted, so the parent can update its clip list. */
  onHookTitleSaved?: (clipId: string, hookTitle: string) => void;
}

export function GeneratePostPanel({ taskId, clipId, onHookTitleSaved }: GeneratePostPanelProps) {
  const t = useTranslations("post");
  const copyText = useCopyText();
  const [hookTitle, setHookTitle] = useState("");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [generating, setGenerating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(true);
  const [copiedField, setCopiedField] = useState<string | null>(null);

  // Load any previously saved draft when the panel mounts (or clip changes).
  const loadDraft = useCallback(async () => {
    try {
      const response = await fetch(
        `/api/tasks/${taskId}/clips/${clipId}/post-draft`,
        { cache: "no-store" },
      );
      if (response.ok) {
        const data = await response.json();
        if (data.hook_title) setHookTitle(data.hook_title);
        if (data.title) setTitle(data.title);
        if (data.description) setDescription(data.description);
      }
    } catch {
      // silent — empty fields are fine
    } finally {
      setLoading(false);
    }
  }, [taskId, clipId]);

  useEffect(() => {
    setLoading(true);
    setHookTitle("");
    setTitle("");
    setDescription("");
    void loadDraft();
  }, [clipId, loadDraft]);

  const copyToClipboard = async (text: string, field: string) => {
    if (!text) return;
    if (await copyText(text) !== "copied") return;
    setCopiedField(field);
    setTimeout(() => setCopiedField(null), 2000);
  };

  const generate = async () => {
    setGenerating(true);
    try {
      const response = await fetch(
        `/api/tasks/${taskId}/clips/${clipId}/generate-post`,
        { method: "POST", headers: { "Content-Type": "application/json" } },
      );
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data?.detail || t("generateFailed"));
      }
      const data = await response.json();
      if (data.hook_title) setHookTitle(data.hook_title);
      if (data.title) setTitle(data.title);
      if (data.description) setDescription(data.description);
      toast.success(t("generateSuccess"));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("generateFailed"));
    } finally {
      setGenerating(false);
    }
  };

  const saveAll = async () => {
    if (!hookTitle.trim() && !title.trim() && !description.trim()) return;
    setSaving(true);
    try {
      const response = await fetch(
        `/api/tasks/${taskId}/clips/${clipId}/hook-title`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            hook_title: hookTitle.trim(),
            title: title.trim(),
            description: description.trim(),
          }),
        },
      );
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data?.detail || t("saveHookFailed"));
      }
      toast.success(t("saveHookSuccess"));
      onHookTitleSaved?.(clipId, hookTitle.trim());
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("saveHookFailed"));
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="rounded-xl border p-4 space-y-3">
      <div className="sticky top-0 z-10 -mx-4 -mt-4 flex flex-wrap items-center justify-between gap-2 border-b bg-background px-4 py-3">
        <span className="text-xs font-medium text-muted-foreground uppercase">
          {t("postDetails")}
        </span>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={generate}
            disabled={generating || loading}
            className="gap-2"
          >
            {generating ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              <Sparkles className="size-3.5" />
            )}
            {generating ? t("generating") : t("generateWithAi")}
          </Button>
          <Button
            size="sm"
            onClick={saveAll}
            disabled={saving || loading || (!hookTitle.trim() && !title.trim() && !description.trim())}
            className="gap-2"
          >
            {saving ? <Loader2 className="size-3.5 animate-spin" /> : <Save className="size-3.5" />}
            {t("saveButton")}
          </Button>
        </div>
      </div>

      {loading && (
        <div className="flex items-center gap-2 py-1 text-xs text-muted-foreground">
          <Loader2 className="size-3 animate-spin" />
          {t("loading") ?? "Loading…"}
        </div>
      )}

      {/* Hook title (on-screen headline for the video) */}
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <label className="text-xs text-muted-foreground">{t("hookTitleLabel")}</label>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => copyToClipboard(hookTitle, "hook_title")}
            className="h-5 px-1.5 text-xs"
            disabled={!hookTitle}
          >
            {copiedField === "hook_title" ? <Check className="size-3" /> : <Copy className="size-3" />}
          </Button>
        </div>
        <Input
          value={hookTitle}
          onChange={(e) => setHookTitle(e.target.value)}
          placeholder={t("hookTitlePlaceholder")}
        />
        <p className="text-[11px] text-muted-foreground">{t("hookTitleHint")}</p>
      </div>

      {/* Post title */}
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <label className="text-xs text-muted-foreground">{t("titleLabel")}</label>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => copyToClipboard(title, "title")}
            className="h-5 px-1.5 text-xs"
            disabled={!title}
          >
            {copiedField === "title" ? <Check className="size-3" /> : <Copy className="size-3" />}
          </Button>
        </div>
        <Input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder={t("titlePlaceholder")}
        />
      </div>

      {/* Description */}
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <label className="text-xs text-muted-foreground">{t("descriptionLabel")}</label>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => copyToClipboard(description, "description")}
            className="h-5 px-1.5 text-xs"
            disabled={!description}
          >
            {copiedField === "description" ? <Check className="size-3" /> : <Copy className="size-3" />}
          </Button>
        </div>
        <Textarea
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder={t("descriptionPlaceholder")}
          rows={4}
        />
      </div>

    </section>
  );
}
