export class JoyHouseBotError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly statusCode?: number,
    readonly retryable = false,
    readonly fieldPath?: string,
  ) {
    super(message);
    this.name = "JoyHouseBotError";
  }

  static fromResponse(status: number, value: unknown): JoyHouseBotError {
    const envelope = value as {error?: Record<string, unknown>} | null;
    const error = envelope?.error;
    return new JoyHouseBotError(
      String(error?.code ?? "HTTP_ERROR"),
      String(error?.message ?? `joyhousebot HTTP ${status}`),
      status,
      Boolean(error?.retryable),
      error?.field_path ? String(error.field_path) : undefined,
    );
  }
}
