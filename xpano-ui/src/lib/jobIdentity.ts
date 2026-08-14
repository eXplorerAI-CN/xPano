function comparablePath(value: string) {
  const displayPath = value.startsWith('\\\\?\\UNC\\')
    ? `\\\\${value.slice('\\\\?\\UNC\\'.length)}`
    : value.startsWith('\\\\?\\')
      ? value.slice('\\\\?\\'.length)
      : value
  return displayPath.replaceAll('\\', '/').replace(/\/+$/, '').toLocaleLowerCase()
}

export function jobIdentityMatchesProject(eventProjectRoot: string | null | undefined, projectRoot: string) {
  if (!projectRoot) return true
  if (!eventProjectRoot) return false
  return comparablePath(eventProjectRoot) === comparablePath(projectRoot)
}
