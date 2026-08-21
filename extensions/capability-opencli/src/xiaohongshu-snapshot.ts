import type {ChildProcess} from "node:child_process";

import {runOpenCli, type OpenCliExecutionResult} from "./runner.js";
import type {CompiledCommand} from "./types.js";

export interface SnapshotProgress {
  completed: number;
  total: number;
  note_id?: string;
  status: "listed" | "complete" | "unavailable";
}

interface SnapshotRunnerOptions {
  entrypoint: string;
  listCommand: CompiledCommand;
  detailCommand: CompiledCommand;
  input: unknown;
  workspace: string;
  timeoutMs: number;
  maxStdoutBytes: number;
  maxStderrBytes: number;
  checkpoint?: unknown;
  onProgress: (
    progress: SnapshotProgress,
    checkpoint: Record<string, unknown>,
  ) => Promise<void>;
}

export interface RunningSnapshot {
  cancel: () => void;
  result: Promise<OpenCliExecutionResult>;
}

interface SnapshotInput {
  profile_url: string;
  limit: number;
  page_delay_seconds: number;
  browser_profile_ref: string;
}

interface ListedNote {
  id: string;
  title: string;
  type: string;
  likes: string;
  cover: string;
  signedUrl: string;
  publicUrl: string;
}

export function runXiaohongshuAccountSnapshot(options: SnapshotRunnerOptions): RunningSnapshot {
  const input = parseInput(options.input);
  let activeChild: ChildProcess | null = null;
  let cancelled = false;
  const cancel = () => {
    cancelled = true;
    activeChild?.kill("SIGTERM");
  };
  const execute = async (
    command: CompiledCommand,
    commandInput: Record<string, unknown>,
    deadline: number,
  ): Promise<OpenCliExecutionResult> => {
    const remaining = deadline - Date.now();
    if (remaining <= 0) return timeoutResult();
    const running = runOpenCli({
      entrypoint: options.entrypoint,
      command,
      input: commandInput,
      workspace: options.workspace,
      timeoutMs: Math.min(command.capability.timeout_seconds * 1_000, remaining),
      maxStdoutBytes: options.maxStdoutBytes,
      maxStderrBytes: options.maxStderrBytes,
    });
    activeChild = running.child;
    const result = await running.result;
    activeChild = null;
    return cancelled ? cancelledResult() : result;
  };
  const result = (async (): Promise<OpenCliExecutionResult> => {
    const deadline = Date.now() + options.timeoutMs;
    const collectedAt = checkpointCollectedAt(options.checkpoint) ?? new Date().toISOString();
    const listed = await execute(options.listCommand, {
      id: input.profile_url,
      limit: input.limit,
      browser_profile_ref: input.browser_profile_ref,
    }, deadline);
    if (listed.state !== "succeeded") return listed;
    const notes = parseListedNotes(listed.output, input.limit);
    const checkpoint = checkpointNotes(options.checkpoint, input);
    const collected: Array<Record<string, unknown>> = [];
    let openedPages = 0;
    for (const note of notes) {
      const existing = checkpoint.get(note.id);
      if (existing) collected.push(existing);
    }
    await options.onProgress(
      {completed: collected.length, total: notes.length, status: "listed"},
      snapshotOutput(input, notes.length, collectedAt, collected),
    );
    for (let index = 0; index < notes.length; index += 1) {
      if (cancelled) return cancelledResult();
      const note = notes[index];
      if (checkpoint.has(note.id)) continue;
      if (openedPages > 0) await wait(input.page_delay_seconds * 1_000);
      if (cancelled) return cancelledResult();
      openedPages += 1;
      const detail = await execute(options.detailCommand, {
        "note-id": note.signedUrl,
        browser_profile_ref: input.browser_profile_ref,
      }, deadline);
      if (["manual_required", "retryable", "cancelled"].includes(detail.state)) return detail;
      if (detail.state === "succeeded") {
        collected.push(completeNote(note, detail.output));
        await options.onProgress({
          completed: collected.length,
          total: notes.length,
          note_id: note.id,
          status: "complete",
        }, snapshotOutput(input, notes.length, collectedAt, collected));
      } else {
        collected.push(unavailableNote(note, detail.error?.code ?? "OPENCLI_COMMAND_FAILED"));
        await options.onProgress({
          completed: collected.length,
          total: notes.length,
          note_id: note.id,
          status: "unavailable",
        }, snapshotOutput(input, notes.length, collectedAt, collected));
      }
    }
    return {
      state: "succeeded",
      output: snapshotOutput(input, notes.length, collectedAt, collected),
    };
  })();
  return {cancel, result};
}

function checkpointNotes(value: unknown, input: SnapshotInput): Map<string, Record<string, unknown>> {
  const result = new Map<string, Record<string, unknown>>();
  if (!value || typeof value !== "object" || Array.isArray(value)) return result;
  const snapshot = value as Record<string, unknown>;
  if (
    snapshot.platform !== "xiaohongshu"
    || snapshot.profile_url !== input.profile_url
    || snapshot.requested_count !== input.limit
    || !Array.isArray(snapshot.notes)
  ) return result;
  for (const raw of snapshot.notes) {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) continue;
    const note = raw as Record<string, unknown>;
    const id = bounded(note.id, 128);
    if (
      !id
      || !["complete", "unavailable"].includes(String(note.status ?? ""))
      || JSON.stringify(note).includes("xsec_token")
    ) continue;
    result.set(id, note);
  }
  return result;
}

function checkpointCollectedAt(value: unknown): string | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const collectedAt = String((value as Record<string, unknown>).collected_at ?? "");
  return /^\d{4}-\d{2}-\d{2}T/.test(collectedAt) ? collectedAt.slice(0, 64) : null;
}

function snapshotOutput(
  input: SnapshotInput,
  discoveredCount: number,
  collectedAt: string,
  notes: Array<Record<string, unknown>>,
): Record<string, unknown> {
  return {
    platform: "xiaohongshu",
    profile_url: input.profile_url,
    collected_at: collectedAt,
    requested_count: input.limit,
    discovered_count: discoveredCount,
    complete_text_count: notes.filter((item) => item.status === "complete").length,
    notes,
  };
}

function parseInput(value: unknown): SnapshotInput {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("Xiaohongshu snapshot input must be an object");
  }
  const item = value as Record<string, unknown>;
  const known = new Set(["profile_url", "limit", "page_delay_seconds", "browser_profile_ref"]);
  const unknown = Object.keys(item).filter((key) => !known.has(key));
  if (unknown.length) throw new Error(`Xiaohongshu snapshot input contains unknown fields: ${unknown.join(", ")}`);
  const profileUrl = String(item.profile_url ?? "");
  if (!/^https:\/\/www\.xiaohongshu\.com\/user\/profile\/[A-Za-z0-9]{1,64}\/?$/.test(profileUrl)) {
    throw new Error("Xiaohongshu snapshot requires an allowlisted profile URL without query parameters");
  }
  const profile = String(item.browser_profile_ref ?? "");
  if (!/^[A-Za-z0-9_.:-]{1,128}$/.test(profile)) {
    throw new Error("Xiaohongshu snapshot requires an explicit local browser profile");
  }
  const limit = item.limit === undefined ? 20 : Number(item.limit);
  const delay = item.page_delay_seconds === undefined ? 4 : Number(item.page_delay_seconds);
  if (!Number.isSafeInteger(limit) || limit < 1 || limit > 20) {
    throw new Error("Xiaohongshu snapshot limit must be between 1 and 20");
  }
  if (!Number.isFinite(delay) || delay < 2 || delay > 15) {
    throw new Error("Xiaohongshu snapshot page delay must be between 2 and 15 seconds");
  }
  return {profile_url: profileUrl, limit, page_delay_seconds: delay, browser_profile_ref: profile};
}

function parseListedNotes(value: unknown, limit: number): ListedNote[] {
  if (!Array.isArray(value)) throw new Error("Xiaohongshu profile returned a malformed note list");
  return value.slice(0, limit).map((raw) => {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
      throw new Error("Xiaohongshu profile returned a malformed note");
    }
    const item = raw as Record<string, unknown>;
    const id = bounded(item.id, 128);
    const signedUrl = bounded(item.url, 4_096);
    const parsed = new URL(signedUrl);
    if (parsed.protocol !== "https:" || parsed.hostname !== "www.xiaohongshu.com" || !id) {
      throw new Error("Xiaohongshu profile returned a note outside the allowlist");
    }
    parsed.search = "";
    parsed.hash = "";
    return {
      id,
      title: bounded(item.title, 2_000),
      type: bounded(item.type, 64),
      likes: bounded(item.likes, 64),
      cover: bounded(item.cover, 4_096),
      signedUrl,
      publicUrl: parsed.toString(),
    };
  });
}

function completeNote(note: ListedNote, value: unknown): Record<string, unknown> {
  const fields = new Map<string, string>();
  if (Array.isArray(value)) {
    for (const raw of value) {
      if (!raw || typeof raw !== "object" || Array.isArray(raw)) continue;
      const row = raw as Record<string, unknown>;
      const field = bounded(row.field, 128);
      if (field) fields.set(field, bounded(row.value, 100_000));
    }
  }
  return {
    id: note.id,
    title: fields.get("title") || note.title,
    author: fields.get("author") ?? "",
    content: fields.get("content") ?? "",
    tags: (fields.get("tags") ?? "").split(",").map((tag) => tag.trim()).filter(Boolean).slice(0, 100),
    type: note.type,
    likes: fields.get("likes") || note.likes,
    collects: fields.get("collects") ?? "0",
    comments: fields.get("comments") ?? "0",
    cover_url: note.cover,
    url: note.publicUrl,
    status: "complete",
    error_code: null,
  };
}

function unavailableNote(note: ListedNote, errorCode: string): Record<string, unknown> {
  return {
    id: note.id,
    title: note.title,
    author: "",
    content: "",
    tags: [],
    type: note.type,
    likes: note.likes,
    collects: "0",
    comments: "0",
    cover_url: note.cover,
    url: note.publicUrl,
    status: "unavailable",
    error_code: errorCode.slice(0, 128),
  };
}

function bounded(value: unknown, maximum: number): string {
  const result = value === undefined || value === null ? "" : String(value).trim();
  return result.slice(0, maximum);
}

function wait(milliseconds: number): Promise<void> {
  return new Promise((done) => setTimeout(done, milliseconds));
}

function timeoutResult(): OpenCliExecutionResult {
  return {
    state: "retryable",
    error: {code: "OPENCLI_TIMEOUT", message: "Account snapshot exceeded its deadline", retryable: true, exit_code: 75},
  };
}

function cancelledResult(): OpenCliExecutionResult {
  return {
    state: "cancelled",
    error: {code: "OPENCLI_CANCELLED", message: "Account snapshot was cancelled", retryable: false, exit_code: 130},
  };
}
