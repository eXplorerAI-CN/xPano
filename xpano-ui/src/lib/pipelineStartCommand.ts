import type { ProjectRunOptions } from './types'

export function pipelineStartCommand(options: ProjectRunOptions) {
  return options.reconstruction ? 'start_reconstruction_job' : 'start_pipeline'
}

export function pipelineInputTracks<T>(tracks: T[], options: ProjectRunOptions): T[] {
  return options.manifestPath ? [] : tracks
}
