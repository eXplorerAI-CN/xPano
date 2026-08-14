import type { Matrix4 } from '../lib/contracts'
import type { PointCloudData, ResolvedTheme } from '../lib/types'
import { processPointCloudPacket } from '../lib/pointCloudProcessing'

interface PointCloudWorkerRequest {
  buffer: ArrayBuffer
  theme: ResolvedTheme
  transform: Matrix4 | null
}

type PointCloudWorkerResponse = { data: PointCloudData } | { error: string }

const scope = self as unknown as {
  onmessage: ((event: MessageEvent<PointCloudWorkerRequest>) => void) | null
  postMessage: (message: PointCloudWorkerResponse, transfer: Transferable[]) => void
}

scope.onmessage = (event) => {
  try {
    const data = processPointCloudPacket(event.data.buffer, event.data.theme, event.data.transform)
    scope.postMessage({ data }, [data.points.buffer, data.colors.buffer])
  } catch (error) {
    scope.postMessage({ error: String(error) }, [])
  }
}
