const API_BASE = '/api'

export interface TranscribeResponse {
  ok: boolean
  text: string
}

/**
 * 上传音频文件到 POST /transcribe，返回转写文本。
 * 若入参为 Blob，需传入 filename（如 recording.webm），后端写临时文件会用到。
 */
export async function transcribeAudio(
  file: File | Blob,
  filename?: string,
): Promise<TranscribeResponse> {
  const formData = new FormData()
  const name = filename ?? (file instanceof File ? file.name : 'recording.webm')
  formData.append('file', file, name)

  const res = await fetch(`${API_BASE}/transcribe`, {
    method: 'POST',
    body: formData,
  })

  if (!res.ok) {
    const text = await res.text()
    let detail = text || `HTTP ${res.status}`
    try {
      const obj = JSON.parse(text) as { detail?: string }
      if (typeof obj.detail === 'string') detail = obj.detail
    } catch {
      // use raw text
    }
    throw new Error(detail)
  }

  const data = (await res.json()) as { ok?: boolean; text?: string }
  return {
    ok: data.ok === true,
    text: typeof data.text === 'string' ? data.text : '',
  }
}
