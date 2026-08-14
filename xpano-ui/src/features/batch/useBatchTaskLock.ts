import { useLocation } from 'react-router-dom'
import { useBatch } from '../../app/useBatch'
import { batchTaskInputLocked } from './batchTypes'

export function useBatchTaskLock() {
  const location = useLocation()
  const { queue } = useBatch()
  const taskId = new URLSearchParams(location.search).get('batchTask')
  const locked = batchTaskInputLocked(queue.tasks, taskId)
  return {
    locked,
    reason: locked ? '该任务已进入批量队列，输入参数已锁定；如需修改，请返回任务列表后停止或重新编辑任务。' : '',
  }
}
