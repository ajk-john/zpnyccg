package io.qzxnbqm.vktr.data.repository

import io.qzxnbqm.vktr.data.model.Module
import io.qzxnbqm.vktr.data.model.ModuleUpdateInfo

interface ModuleRepository {
    suspend fun getModules(): Result<List<Module>>
    suspend fun checkUpdate(module: Module): Result<ModuleUpdateInfo>
}
