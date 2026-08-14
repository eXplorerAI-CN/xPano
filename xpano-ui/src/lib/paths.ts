export function normalizeDisplayPath(path: string) {
  if (path.startsWith('\\\\?\\UNC\\')) return `\\\\${path.slice('\\\\?\\UNC\\'.length)}`
  if (path.startsWith('\\\\?\\')) return path.slice('\\\\?\\'.length)
  return path
}

export function joinDisplayPath(root: string, relative: string) {
  const separator = root.includes('\\') ? '\\' : '/'
  return normalizeDisplayPath(`${root.replace(/[\\/]+$/, '')}${separator}${relative.replace(/^[\\/]+/, '').replace(/[\\/]/g, separator)}`)
}
