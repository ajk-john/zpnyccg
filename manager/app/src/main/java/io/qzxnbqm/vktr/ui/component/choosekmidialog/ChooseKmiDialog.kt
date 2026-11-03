package io.qzxnbqm.vktr.ui.component.choosekmidialog

import androidx.compose.runtime.Composable
import io.qzxnbqm.vktr.ui.LocalUiMode
import io.qzxnbqm.vktr.ui.UiMode

@Composable
fun ChooseKmiDialog(
    show: Boolean,
    onDismissRequest: () -> Unit,
    onSelected: (String?) -> Unit
) {
    when (LocalUiMode.current) {
        UiMode.Miuix -> ChooseKmiDialogMiuix(show, onDismissRequest, onSelected)
        UiMode.Material -> ChooseKmiDialogMaterial(show, onDismissRequest, onSelected)
    }
}
