const RESULTS_PATH = '/project/results'

export function resultsWorkspaceState(wasMounted: boolean, pathname: string) {
  const active = pathname === RESULTS_PATH
  return { active, mounted: wasMounted || active }
}
