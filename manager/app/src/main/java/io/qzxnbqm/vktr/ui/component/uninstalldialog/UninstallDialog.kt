package io.qzxnbqm.vktr.ui.component.uninstalldialog

import androidx.compose.runtime.Composable
import io.qzxnbqm.vktr.ui.LocalUiMode
import io.qzxnbqm.vktr.ui.UiMode

@Composable
fun UninstallDialog(
    show: Boolean,
    onDismissRequest: () -> Unit
) {
    when (LocalUiMode.current) {
        UiMode.Miuix -> UninstallDialogMiuix(show, onDismissRequest)
        UiMode.Material -> UninstallDialogMaterial(show, onDismissRequest)
    }
}
