"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import { Check, Copy, Loader2, Sparkles, Save } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

interface GeneratePostPanelProps {
  taskId: string;
  clipId: string;
}

export function GeneratePostPanel({ taskId, clipId }: GeneratePostPanelProps) {
  const t = useTranslations("post");
  const [hookTitle, setHookTitle] = useState("");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [generating, setGenerating] = useState(false);
  const [savingHook, setSavingHook] = useState(false);
  const [copiedField, setCopiedField] = useState<string | null>(null);

  const copyToClipboard = async (text: string, field: string) => {
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopiedField(field);
      setTimeout(() => setCopiedField(null), 2000);
    } catch {
      // silent
    }
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

  const applyHookTitle = async () => {
    if (!hookTitle.trim()) return;
    setSavingHook(true);
    try {
      const response = await fetch(
        `/api/tasks/${taskId}/clips/${clipId}/hook-title`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ hook_title: hookTitle.trim() }),
        },
      );
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data?.detail || t("saveHookFailed"));
      }
      toast.success(t("saveHookSuccess"));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("saveHookFailed"));
    } finally {
      setSavingHook(false);
    }
  };

  return (
    <section className="rounded-xl border p-4 space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-muted-foreground uppercase">
          {t("postDetails")}
        </span>
        <Button
          variant="outline"
          size="sm"
          onClick={generate}
          disabled={generating}
          className="gap-2"
        >
          {generating ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <Sparkles className="size-3.5" />
          )}
          {generating ? t("generating") : t("generateWithAi")}
        </Button>
      </div>

      {/* Hook title (on-screen headline for the video) */}
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <label className="text-xs text-muted-foreground">{t("hookTitleLabel")}</label>
          <div className="flex items-center gap-1">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => copyToClipboard(hookTitle, "hook_title")}
              className="h-5 px-1.5 text-xs"
              disabled={!hookTitle}
            >
              {copiedField === "hook_title" ? <Check className="size-3" /> : <Copy className="size-3" />}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={applyHookTitle}
              className="h-5 px-1.5 text-xs"
              disabled={!hookTitle.trim() || savingHook}
            >
              {savingHook ? <Loader2 className="size-3 animate-spin" /> : <Save className="size-3" />}
              {t("applyToClip")}
            </Button>
          </div>
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
