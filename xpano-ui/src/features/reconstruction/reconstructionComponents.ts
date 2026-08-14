export interface ComponentSummary {
  componentKey: string
  label: string
  alignedCameraCount: number
  totalCameraCount: number
  tiePointCount: number
  isInitiallyActive: boolean
}

export interface ComponentInspection {
  schemaVersion: number
  inventoryComplete: boolean
  totalCameras: number
  alignedCameras: number
  unalignedCameras: number
  defaultComponentKey: string
  components: ComponentSummary[]
  warnings: string[]
}

export interface ComponentSelectionDecision {
  mode: 'direct' | 'confirm'
  selectedComponentKey: string
  currentExportedComponentKey: string
  inspection: ComponentInspection
}

export function prepareComponentSelection(
  inspection: ComponentInspection,
  currentExportedComponentKey = '',
): ComponentSelectionDecision {
  const usable = inspection.components.filter((component) => (
    component.componentKey.trim().length > 0 && component.alignedCameraCount > 0
  ))
  const selected = usable.find((component) => (
    component.componentKey === inspection.defaultComponentKey
  ))
  if (!selected) throw new Error('PSX 中没有可导出的 Component，请重新检查 Metashape 工程')

  return {
    mode: usable.length === 1 ? 'direct' : 'confirm',
    selectedComponentKey: selected.componentKey,
    currentExportedComponentKey,
    inspection: { ...inspection, components: usable },
  }
}
