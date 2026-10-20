import { request, requestText } from './api'
import { createMultiExperimentApi } from './multiExperimentRequests'

export const multiExperimentApi = createMultiExperimentApi(request, requestText)
