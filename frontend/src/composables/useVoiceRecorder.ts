import { ref, type Ref } from 'vue'

/**
 * 浏览器内录音：getUserMedia + MediaRecorder，收集 chunks，停止时返回 Blob。
 * 暴露 recording 状态、startRecording、stopRecording（返回 Promise<Blob | null>）。
 */
export function useVoiceRecorder() {
  const recording = ref(false) as Ref<boolean>
  let mediaRecorder: MediaRecorder | null = null
  let stream: MediaStream | null = null
  const chunks: Blob[] = []
  let resolveBlob: ((blob: Blob | null) => void) | null = null
  let blobPromise: Promise<Blob | null> | null = null

  function startRecording(): Promise<void> {
    if (recording.value) return Promise.resolve()
    return navigator.mediaDevices
      .getUserMedia({ audio: true })
      .then((s) => {
        stream = s
        const mimeType = MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm' : undefined
        mediaRecorder = new MediaRecorder(s, mimeType ? { mimeType } : {})
        chunks.length = 0
        mediaRecorder.ondataavailable = (e) => {
          if (e.data.size > 0) chunks.push(e.data)
        }
        blobPromise = new Promise<Blob | null>((resolve) => {
          resolveBlob = resolve
        })
        mediaRecorder.onstop = () => {
          const blob = chunks.length > 0
            ? new Blob(chunks, { type: mediaRecorder?.mimeType || 'audio/webm' })
            : null
          if (resolveBlob) resolveBlob(blob)
          resolveBlob = null
          stream?.getTracks().forEach((t) => t.stop())
          stream = null
          mediaRecorder = null
        }
        mediaRecorder.start()
        recording.value = true
      })
  }

  function stopRecording(): Promise<Blob | null> {
    if (!recording.value || !mediaRecorder) return Promise.resolve(null)
    recording.value = false
    if (mediaRecorder.state === 'recording') mediaRecorder.stop()
    return blobPromise ?? Promise.resolve(null)
  }

  return { recording, startRecording, stopRecording }
}
