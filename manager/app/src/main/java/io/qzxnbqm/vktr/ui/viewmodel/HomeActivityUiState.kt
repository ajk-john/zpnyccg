package io.qzxnbqm.vktr.ui.viewmodel

import androidx.compose.runtime.Immutable
import io.qzxnbqm.vktr.ui.UiMode
import io.qzxnbqm.vktr.ui.theme.AppSettings

@Immutable
data class HomeActivityUiState(
    val appSettings: AppSettings,
    val pageScale: Float,
    val enableBlur: Boolean,
    val enableFloatingBottomBar: Boolean,
    val enableFloatingBottomBarBlur: Boolean,
    val enableNavigationBadge: Boolean,
    val enableSwipeDismiss: Boolean,
    val pagerInterceptionMode: Int,
    val moduleDescriptionMaxLines: Int = 4,
    val uiMode: UiMode,
)
