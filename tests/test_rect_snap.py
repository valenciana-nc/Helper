"""Tests for rect_snap.snap_to_control and help_session.looks_oversized."""
from __future__ import annotations

import json
import unittest
from unittest.mock import patch


class _FakeRect:
    __slots__ = ("left", "top", "right", "bottom")

    def __init__(self, left: int, top: int, right: int, bottom: int) -> None:
        self.left = left
        self.top = top
        self.right = right
        self.bottom = bottom


class _FakeElementInfo:
    def __init__(
        self,
        *,
        control_type: str = "",
        name: str = "",
        automation_id: str = "",
        rectangle: _FakeRect | None = None,
        handle: int | None = None,
        enabled: bool = True,
        visible: bool = True,
    ) -> None:
        self.control_type = control_type
        self.name = name
        self.automation_id = automation_id
        self.rectangle = rectangle
        self.handle = handle
        self.enabled = enabled
        self.visible = visible


class _FakeControl:
    def __init__(
        self,
        *,
        text: str = "",
        control_type: str = "",
        rect: _FakeRect | None = None,
        automation_id: str = "",
        handle: int | None = None,
        enabled: bool = True,
        visible: bool = True,
        children: list["_FakeControl"] | None = None,
    ) -> None:
        self._text = text
        self.handle = handle
        self._children = list(children or [])
        self.element_info = _FakeElementInfo(
            control_type=control_type,
            name=text,
            automation_id=automation_id,
            rectangle=rect,
            handle=handle,
            enabled=enabled,
            visible=visible,
        )

    def window_text(self) -> str:
        return self._text

    def children(self) -> list["_FakeControl"]:
        return list(self._children)

    def is_enabled(self) -> bool:
        return bool(self.element_info.enabled)

    def is_visible(self) -> bool:
        return bool(self.element_info.visible)


class _FakeDesktop:
    def __init__(self, toplevels: list[_FakeControl]) -> None:
        self._toplevels = list(toplevels)

    def windows(self, **_kwargs: object) -> list[_FakeControl]:
        return list(self._toplevels)


class _RecordingControl(_FakeControl):
    def __init__(self, *, visits: list[str], **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._visits = visits

    def children(self) -> list[_FakeControl]:
        self._visits.append(self.window_text())
        return super().children()


def _make_button(
    name: str,
    x: int,
    y: int,
    w: int,
    h: int,
    *,
    control_type: str = "Button",
    automation_id: str = "",
) -> _FakeControl:
    return _FakeControl(
        text=name,
        control_type=control_type,
        rect=_FakeRect(x, y, x + w, y + h),
        automation_id=automation_id,
    )


def _make_window(
    name: str,
    x: int,
    y: int,
    w: int,
    h: int,
    children: list[_FakeControl],
    *,
    handle: int | None = None,
) -> _FakeControl:
    return _FakeControl(
        text=name,
        control_type="Window",
        rect=_FakeRect(x, y, x + w, y + h),
        handle=handle,
        children=children,
    )


class HelpIntentLanguageTests(unittest.TestCase):
    def test_file_action_aliases_expand_to_browse_language(self) -> None:
        from help_intents import tokenize_control, tokenize_instruction

        tokens = tokenize_instruction("Upload a file")

        self.assertIn("browse", tokens)
        self.assertIn("choose", tokens)
        self.assertIn("attach", tokens)
        self.assertIn("paperclip", tokenize_instruction("Attach a file"))
        self.assertTrue(
            {"attach", "attachment", "paperclip"}.issubset(tokenize_control("Paperclip"))
        )
        for icon in ("\U0001f4ce", "\U0001f587"):
            with self.subTest(icon=icon):
                icon_tokens = tokenize_control(icon)
                self.assertTrue({"attach", "attachment", "paperclip"}.issubset(icon_tokens))
                self.assertNotIn("paste", icon_tokens)

    def test_copy_action_aliases_expand_to_duplicate_language(self) -> None:
        from help_intents import tokenize_instruction, tokenize_control

        copy_tokens = tokenize_instruction("Copy this item")
        clone_tokens = tokenize_instruction("Clone this item")
        duplicate_tokens = tokenize_control("Duplicate")

        self.assertTrue({"clone", "copy", "duplicate"}.issubset(copy_tokens))
        self.assertTrue({"clone", "copy", "duplicate"}.issubset(clone_tokens))
        self.assertTrue({"clone", "copy", "duplicate"}.issubset(duplicate_tokens))

    def test_create_and_completion_aliases_expand_to_common_button_language(self) -> None:
        from help_intents import tokenize_instruction, tokenize_control

        create_tokens = tokenize_instruction("Create item")
        finish_tokens = tokenize_instruction("Finish setup")
        checkmark_tokens = tokenize_instruction("Click the check mark")

        self.assertTrue({"add", "create", "new"}.issubset(create_tokens))
        self.assertTrue({"add", "create", "new"}.issubset(tokenize_instruction("New item")))
        self.assertTrue({"add", "create", "new"}.issubset(tokenize_control("Add")))
        self.assertTrue({"complete", "done", "finish"}.issubset(finish_tokens))
        self.assertTrue({"complete", "done", "finish"}.issubset(tokenize_instruction("Complete setup")))
        self.assertTrue({"complete", "done", "finish"}.issubset(tokenize_control("Done")))
        self.assertTrue({"checkmark", "complete", "done", "finish"}.issubset(checkmark_tokens))
        self.assertNotIn("mark", checkmark_tokens)
        for icon in ("\u2705", "\u2713", "\u2714"):
            with self.subTest(icon=icon):
                self.assertTrue(
                    {"checkmark", "confirm", "done", "ok"}.issubset(tokenize_control(icon))
                )

    def test_auth_direction_aliases_do_not_cross_sign_in_and_out(self) -> None:
        from help_intents import tokenize_instruction, tokenize_control

        sign_out_tokens = tokenize_instruction("Sign out")
        log_out_tokens = tokenize_control("Logout")
        sign_in_tokens = tokenize_instruction("Sign in")
        log_in_tokens = tokenize_control("Log in")

        self.assertTrue({"logoff", "logout", "out", "signout"}.issubset(sign_out_tokens))
        self.assertTrue({"logoff", "logout", "out", "signout"}.issubset(log_out_tokens))
        self.assertNotIn("login", sign_out_tokens)
        self.assertNotIn("signin", log_out_tokens)
        self.assertTrue({"login", "signin"}.issubset(sign_in_tokens))
        self.assertTrue({"login", "signin"}.issubset(log_in_tokens))
        self.assertNotIn("logout", sign_in_tokens)
        self.assertNotIn("signout", log_in_tokens)

    def test_dialog_dismiss_aliases_stay_contextual(self) -> None:
        from help_intents import tokenize_instruction, tokenize_control

        close_dialog_tokens = tokenize_instruction("Close the dialog")
        dismiss_modal_tokens = tokenize_instruction("Dismiss modal")
        cancel_dialog_tokens = tokenize_instruction("Cancel the dialog")
        cancel_subscription_tokens = tokenize_instruction("Cancel subscription")

        self.assertEqual(close_dialog_tokens, {"cancel", "close", "dismiss"})
        self.assertEqual(dismiss_modal_tokens, {"cancel", "close", "dismiss"})
        self.assertEqual(cancel_dialog_tokens, {"cancel", "close", "dismiss"})
        self.assertIn("cancel", tokenize_control("Cancel"))
        self.assertNotIn("close", cancel_subscription_tokens)
        self.assertNotIn("dismiss", cancel_subscription_tokens)

    def test_confirm_aliases_expand_to_apply_and_ok_language(self) -> None:
        from help_intents import instruction_control_intents, tokenize_control, tokenize_instruction

        confirm_tokens = tokenize_instruction("Confirm selection")
        apply_tokens = tokenize_instruction("Apply changes")
        checkmark_intents = instruction_control_intents("Click the check mark")

        self.assertTrue({"apply", "confirm", "ok", "okay"}.issubset(confirm_tokens))
        self.assertTrue({"apply", "confirm", "ok", "okay"}.issubset(apply_tokens))
        self.assertTrue({"apply", "confirm", "ok", "okay"}.issubset(tokenize_control("OK")))
        self.assertTrue({"apply", "confirm", "ok", "okay"}.issubset(tokenize_control("Apply")))
        self.assertTrue({"button", "splitbutton", "menuitem"}.issubset(checkmark_intents))
        self.assertNotIn("checkbox", checkmark_intents)

    def test_clipboard_action_aliases_expand_to_common_icon_language(self) -> None:
        from help_intents import tokenize_instruction, tokenize_control

        self.assertIn("clipboard", tokenize_instruction("Paste into the note"))
        self.assertIn("scissors", tokenize_instruction("Cut selection"))
        self.assertIn("cut", tokenize_instruction("Click scissors"))
        self.assertIn("cut", tokenize_control("Scissors"))

    def test_transfer_and_refresh_aliases_expand_to_matching_language(self) -> None:
        from help_intents import tokenize_instruction, tokenize_control

        self.assertIn("export", tokenize_instruction("Download the report"))
        self.assertIn("download", tokenize_control("Export"))
        self.assertIn("upload", tokenize_instruction("Import data"))
        self.assertIn("import", tokenize_control("Upload"))
        self.assertIn("reload", tokenize_instruction("Refresh the page"))
        self.assertIn("refresh", tokenize_control("Reload"))
        for icon in ("\u27f2", "\u27f3", "\U0001f503", "\U0001f504"):
            with self.subTest(icon=icon):
                self.assertTrue({"refresh", "reload"}.issubset(tokenize_control(icon)))
        self.assertNotIn("refresh", tokenize_control("\u21bb"))

    def test_share_and_archive_aliases_expand_to_common_icon_language(self) -> None:
        from help_intents import tokenize_control

        self.assertIn("share", tokenize_control("\U0001f517"))
        self.assertIn("archive", tokenize_control("File cabinet"))
        self.assertIn("archive", tokenize_control("Filing cabinet"))

    def test_external_link_aliases_expand_to_open_new_language(self) -> None:
        from help_intents import instruction_control_intents, tokenize_control, tokenize_instruction

        external_tokens = tokenize_instruction("Open external link")
        new_tab_tokens = tokenize_instruction("Open in new tab")
        new_window_tokens = tokenize_instruction("Open in new window")
        bare_new_tab_tokens = tokenize_instruction("New tab")
        intents = instruction_control_intents("Open in new tab")

        self.assertTrue({"external", "launch", "open_new"}.issubset(external_tokens))
        self.assertTrue({"external", "new_tab", "open_new"}.issubset(new_tab_tokens))
        self.assertTrue({"external", "new_window", "open_new"}.issubset(new_window_tokens))
        self.assertNotIn("plus", new_tab_tokens)
        self.assertIn("plus", bare_new_tab_tokens)
        self.assertTrue({"button", "splitbutton", "hyperlink", "menuitem"}.issubset(intents))
        self.assertTrue({"external", "new_tab", "open_new"}.issubset(tokenize_control("New tab")))
        for icon in ("\u2197", "\u2b08", "\u29c9"):
            with self.subTest(icon=icon):
                self.assertTrue(
                    {"external", "launch", "open_new"}.issubset(tokenize_control(icon))
                )
                self.assertNotIn("share", tokenize_control(icon))
        self.assertNotIn("external", tokenize_control("\U0001f517"))

    def test_filter_and_sort_aliases_expand_to_toolbar_language(self) -> None:
        from help_intents import tokenize_instruction, tokenize_control

        self.assertIn("funnel", tokenize_instruction("Filter results"))
        self.assertIn("filter", tokenize_instruction("Click funnel"))
        self.assertTrue({"ascending", "sort"}.issubset(tokenize_instruction("Click A to Z")))
        self.assertTrue({"descending", "sort"}.issubset(tokenize_instruction("Click Z to A")))
        self.assertTrue({"ascending", "sort"}.issubset(tokenize_control("A to Z")))
        self.assertTrue({"descending", "sort"}.issubset(tokenize_control("Z to A")))

    def test_editor_toolbar_aliases_expand_to_format_and_history_language(self) -> None:
        from help_intents import instruction_control_intents, tokenize_control, tokenize_instruction

        bold_intents = instruction_control_intents("Bold text")
        click_b_intents = instruction_control_intents("Click B")
        italicize_intents = instruction_control_intents("Italicize selected text")
        copy_intents = instruction_control_intents("Copy selected text")
        paste_intents = instruction_control_intents("Paste selected text")
        remove_formatting_tokens = tokenize_instruction("Remove formatting")

        self.assertTrue({"b", "bold"}.issubset(tokenize_instruction("Bold text")))
        self.assertTrue({"b", "bold"}.issubset(tokenize_instruction("Click B")))
        self.assertTrue({"i", "italic"}.issubset(tokenize_instruction("Italic text")))
        self.assertTrue({"i", "italic"}.issubset(tokenize_instruction("Italicize selected text")))
        self.assertTrue({"u", "underline"}.issubset(tokenize_instruction("Underline text")))
        self.assertTrue({"clear", "formatting"}.issubset(remove_formatting_tokens))
        self.assertFalse(
            remove_formatting_tokens & {"bin", "delete", "remove", "trash", "wastebasket"}
        )
        self.assertTrue({"b", "bold"}.issubset(tokenize_control("B")))
        self.assertTrue({"i", "italic"}.issubset(tokenize_control("I")))
        self.assertTrue({"u", "underline"}.issubset(tokenize_control("U")))
        self.assertTrue({"clear", "formatting"}.issubset(tokenize_control("Remove formatting")))
        self.assertFalse(
            tokenize_control("Remove formatting")
            & {"bin", "delete", "remove", "trash", "wastebasket"}
        )
        self.assertTrue({"button", "splitbutton", "menuitem"}.issubset(bold_intents))
        self.assertTrue({"button", "splitbutton", "menuitem"}.issubset(click_b_intents))
        self.assertTrue({"button", "splitbutton", "menuitem"}.issubset(italicize_intents))
        self.assertTrue({"button", "splitbutton", "menuitem"}.issubset(copy_intents))
        self.assertTrue({"button", "splitbutton", "menuitem"}.issubset(paste_intents))
        self.assertNotIn("edit", bold_intents)
        self.assertNotIn("edit", italicize_intents)
        self.assertNotIn("edit", copy_intents)
        self.assertNotIn("edit", paste_intents)
        self.assertIn("undo", tokenize_control("\u21b6"))
        self.assertIn("redo", tokenize_control("\u21b7"))
        self.assertIn("undo", tokenize_control("Ctrl+Z"))
        self.assertIn("redo", tokenize_control("Ctrl+Y"))
        self.assertIn("redo", tokenize_control("Ctrl+Shift+Z"))

    def test_clear_and_delete_aliases_expand_to_action_icon_language(self) -> None:
        from help_intents import instruction_control_intents, tokenize_control, tokenize_instruction

        clear_text_tokens = tokenize_instruction("Clear text")
        clear_text_intents = instruction_control_intents("Clear text")
        delete_selected_text_intents = instruction_control_intents("Delete selected text")
        delete_tokens = tokenize_instruction("Delete item")

        self.assertTrue({"clear", "x"}.issubset(clear_text_tokens))
        self.assertNotIn("text", clear_text_tokens)
        self.assertTrue({"button", "splitbutton"}.issubset(clear_text_intents))
        self.assertNotIn("edit", clear_text_intents)
        self.assertTrue({"button", "splitbutton", "menuitem"}.issubset(delete_selected_text_intents))
        self.assertNotIn("edit", delete_selected_text_intents)
        self.assertIn("clear", tokenize_control("\u00d7"))
        self.assertIn("clear", tokenize_control("X"))
        self.assertIn("close", tokenize_control("X"))
        self.assertTrue({"bin", "delete", "remove", "trash", "wastebasket"}.issubset(delete_tokens))
        self.assertTrue(
            {"bin", "delete", "remove", "trash", "wastebasket"}.issubset(
                tokenize_control("\U0001f5d1")
            )
        )
        self.assertIn("delete", tokenize_control("Wastebasket"))

    def test_zoom_aliases_expand_to_directional_icon_language(self) -> None:
        from help_intents import instruction_control_intents, tokenize_control, tokenize_instruction

        zoom_in_tokens = tokenize_instruction("Zoom in")
        zoom_out_tokens = tokenize_instruction("Zoom out")

        self.assertEqual(zoom_in_tokens, {"zoom_in"})
        self.assertEqual(zoom_out_tokens, {"zoom_out"})
        self.assertTrue(
            {"button", "splitbutton", "menuitem"}.issubset(
                instruction_control_intents("Zoom in")
            )
        )
        self.assertIn("zoom_in", tokenize_control("+"))
        self.assertIn("zoom_in", tokenize_control("Plus"))
        self.assertIn("zoom_out", tokenize_control("-"))
        self.assertIn("zoom_out", tokenize_control("\u2212"))
        self.assertIn("zoom_out", tokenize_control("Minus"))
        self.assertNotIn("zoom_in", tokenize_control("Add"))
        self.assertNotIn("zoom_out", tokenize_control("Remove"))
        self.assertNotIn("zoom_out", tokenize_control("搜索 - 世界珊瑚礁日"))
        self.assertNotIn("minimize", tokenize_control("搜索 - 世界珊瑚礁日"))
        self.assertNotIn("minus", tokenize_control("搜索 - 世界珊瑚礁日"))

    def test_window_control_aliases_expand_to_caption_icon_language(self) -> None:
        from help_intents import tokenize_control, tokenize_instruction

        self.assertTrue({"minimize", "minus"}.issubset(tokenize_instruction("Minimize window")))
        self.assertEqual(tokenize_instruction("Minimize all windows"), {"show_desktop"})
        self.assertEqual(tokenize_instruction("Minimise all windows"), {"show_desktop"})
        self.assertEqual(tokenize_instruction("Hide all windows"), {"show_desktop"})
        self.assertIn("show_desktop", tokenize_instruction("Show desktop"))
        self.assertTrue({"minimize", "minus"}.issubset(tokenize_instruction("Minimise window")))
        self.assertIn("maximize", tokenize_instruction("Maximize window"))
        self.assertTrue({"restore", "overlap"}.issubset(tokenize_instruction("Restore window")))
        self.assertIn("show_desktop", tokenize_control("Show Desktop"))
        self.assertIn("minimize", tokenize_control("-"))
        self.assertIn("minimize", tokenize_control("\u2212"))
        self.assertIn("minimize", tokenize_control("\U0001f5d5"))
        self.assertIn("maximize", tokenize_control("\u25a1"))
        self.assertIn("maximize", tokenize_control("\u25a2"))
        self.assertIn("maximize", tokenize_control("\u2b1c"))
        self.assertIn("maximize", tokenize_control("\U0001f5d6"))
        self.assertIn("restore", tokenize_control("\U0001f5d7"))
        self.assertNotIn("zoom_out", tokenize_control("Minimize"))
        self.assertNotIn("minimize", tokenize_control("Zoom out"))
        self.assertNotIn("show_desktop", tokenize_instruction("Minimize window"))
        self.assertNotIn("show_desktop", tokenize_instruction("Open desktop"))
        self.assertNotIn("minimize", tokenize_instruction("Minimize all windows"))

    def test_send_action_aliases_expand_to_submit_language(self) -> None:
        from help_intents import tokenize_instruction, tokenize_control

        self.assertIn("submit", tokenize_instruction("Send the message"))
        self.assertIn("plane", tokenize_instruction("Send the message"))
        self.assertIn("send", tokenize_instruction("Submit the form"))
        self.assertIn("send", tokenize_control("Submit"))
        self.assertIn("submit", tokenize_control("Send"))
        self.assertIn("send", tokenize_control("Paper plane"))

    def test_meeting_control_aliases_expand_to_common_labels(self) -> None:
        from help_intents import tokenize_instruction, tokenize_control

        self.assertIn("mic", tokenize_instruction("Mute microphone"))
        self.assertIn("microphone", tokenize_instruction("Mute mic"))
        self.assertIn("video", tokenize_instruction("Start camera"))
        self.assertIn("camera", tokenize_instruction("Start video"))
        self.assertIn("webcam", tokenize_control("Camera"))
        self.assertIn("camera", tokenize_control("Webcam"))

    def test_audio_output_aliases_expand_to_speaker_language_contextually(self) -> None:
        from help_intents import tokenize_instruction, tokenize_control

        mute_audio_tokens = tokenize_instruction("Mute audio")
        audio_settings_tokens = tokenize_instruction("Open audio settings")

        self.assertTrue({"speaker", "sound", "volume"}.issubset(mute_audio_tokens))
        self.assertNotIn("audio", mute_audio_tokens)
        self.assertNotIn("speaker", audio_settings_tokens)
        self.assertIn("sound", tokenize_instruction("Open volume"))
        self.assertIn("speaker", tokenize_control("Sound"))
        self.assertIn("volume", tokenize_control("Speaker"))

    def test_media_control_intents_do_not_expand_video_to_camera(self) -> None:
        from help_intents import tokenize_instruction

        play_tokens = tokenize_instruction("Play video")
        pause_tokens = tokenize_instruction("Pause video")
        resume_tokens = tokenize_instruction("Resume playback")
        start_tokens = tokenize_instruction("Start video")

        self.assertIn("play", play_tokens)
        self.assertNotIn("camera", play_tokens)
        self.assertIn("pause", pause_tokens)
        self.assertNotIn("camera", pause_tokens)
        self.assertIn("play", resume_tokens)
        self.assertNotIn("playback", resume_tokens)
        self.assertIn("camera", start_tokens)

    def test_edit_action_intent_splits_button_action_from_edit_control(self) -> None:
        from help_intents import instruction_control_intents, tokenize_control, tokenize_instruction

        edit_row_tokens = tokenize_instruction("Edit this row")
        edit_row_intents = instruction_control_intents("Edit this row")
        literal_edit_intents = instruction_control_intents("Click this edit control")
        search_field_intents = instruction_control_intents("Click the Search field")
        search_field_button_intents = instruction_control_intents("Click the Search field button")

        self.assertTrue({"edit", "pencil"}.issubset(edit_row_tokens))
        self.assertTrue({"button", "splitbutton", "hyperlink", "menuitem"}.issubset(edit_row_intents))
        self.assertNotIn("edit", edit_row_intents)
        self.assertEqual(literal_edit_intents, {"edit"})
        self.assertEqual(search_field_intents, {"combobox", "edit", "spinner"})
        self.assertEqual(search_field_button_intents, {"button", "splitbutton"})
        self.assertEqual(instruction_control_intents("Type in the Email text field."), {"edit"})
        self.assertEqual(instruction_control_intents("Enter Email in the text box."), {"edit"})
        self.assertIn("edit", tokenize_control("Pencil"))
        self.assertIn("pencil", tokenize_control("Edit"))

    def test_row_wording_sets_listitem_intent_without_stealing_action_rows(self) -> None:
        from help_intents import instruction_control_intents

        self.assertEqual(instruction_control_intents("Click the Settings table row."), {"listitem"})
        self.assertEqual(instruction_control_intents("Click this table row."), {"listitem"})
        self.assertNotIn("listitem", instruction_control_intents("Edit this row."))

    def test_select_button_wording_preserves_explicit_button_intent(self) -> None:
        from help_intents import instruction_control_intents

        intents = instruction_control_intents("Select the Settings button.")

        self.assertEqual(intents, {"button", "splitbutton"})

    def test_row_scoped_menu_wording_targets_menu_launcher_button(self) -> None:
        from help_intents import instruction_control_intents

        self.assertEqual(instruction_control_intents("Open menu in row."), {"button", "splitbutton"})
        self.assertEqual(
            instruction_control_intents("Open menu item in row."),
            {"listitem", "menuitem", "splitbutton"},
        )

    def test_cart_action_aliases_expand_to_basket_language(self) -> None:
        from help_intents import tokenize_instruction, tokenize_control

        self.assertIn("basket", tokenize_instruction("Open cart"))
        self.assertIn("cart", tokenize_instruction("Open basket"))
        self.assertIn("bag", tokenize_instruction("Open cart"))
        self.assertIn("cart", tokenize_control("Basket"))
        self.assertIn("basket", tokenize_control("Shopping bag"))

    def test_password_visibility_intent_is_contextual(self) -> None:
        from help_intents import instruction_control_intents, tokenize_instruction, tokenize_control

        password_tokens = tokenize_instruction("Show password")
        sidebar_tokens = tokenize_instruction("Show sidebar")
        password_field_intents = instruction_control_intents("Focus password field")
        visibility_intents = instruction_control_intents("Show password")

        self.assertTrue({"eye", "visibility", "visible"}.issubset(password_tokens))
        self.assertNotIn("eye", sidebar_tokens)
        self.assertTrue({"button", "splitbutton"}.issubset(visibility_intents))
        self.assertNotIn("edit", visibility_intents)
        self.assertIn("edit", password_field_intents)
        self.assertIn("visibility", tokenize_control("Eye"))
        self.assertIn("eye", tokenize_control("Visibility"))

    def test_security_control_aliases_expand_to_lock_and_shield_language(self) -> None:
        from help_intents import tokenize_instruction, tokenize_control

        self.assertIn("padlock", tokenize_instruction("Lock screen"))
        self.assertIn("lock", tokenize_instruction("Click the padlock"))
        self.assertIn("lock", tokenize_instruction("Unlock account"))
        self.assertIn("shield", tokenize_instruction("Open security"))
        self.assertIn("security", tokenize_instruction("Click shield"))
        self.assertIn("lock", tokenize_control("Padlock"))
        self.assertIn("security", tokenize_control("Shield"))

    def test_site_information_lock_icon_wording_is_specific(self) -> None:
        from help_intents import tokenize_control, tokenize_instruction

        lock_icon_tokens = tokenize_instruction("Click the lock icon")
        padlock_icon_tokens = tokenize_instruction("Click the padlock icon")
        site_info_tokens = tokenize_control("View site information")

        self.assertEqual(lock_icon_tokens, {"site_info_lock"})
        self.assertEqual(padlock_icon_tokens, {"site_info_lock"})
        self.assertIn("site_info_lock", site_info_tokens)
        self.assertIn("site_info_lock", tokenize_control("\U0001f512"))
        self.assertNotIn("site_info_lock", tokenize_instruction("Lock screen"))
        self.assertNotIn("site_info_lock", tokenize_instruction("Unlock account"))
        self.assertNotIn("lock", site_info_tokens)

    def test_navigation_and_time_aliases_expand_to_common_labels(self) -> None:
        from help_intents import tokenize_instruction, tokenize_control

        self.assertIn("date", tokenize_instruction("Open calendar"))
        self.assertIn("calendar", tokenize_instruction("Open date picker"))
        self.assertIn("time", tokenize_instruction("Open clock"))
        self.assertIn("clock", tokenize_instruction("Open time picker"))
        self.assertIn("house", tokenize_instruction("Go home"))
        self.assertIn("home", tokenize_instruction("Click the house"))
        self.assertIn("calendar", tokenize_control("Date"))
        self.assertIn("home", tokenize_control("House"))

    def test_network_aliases_expand_without_starlink_bookmark_collision(self) -> None:
        from help_intents import tokenize_control, tokenize_instruction

        expected = {"internet", "network", "wifi", "wireless"}

        self.assertTrue(expected.issubset(tokenize_instruction("Open Wi-Fi")))
        self.assertTrue(expected.issubset(tokenize_instruction("Open wifi")))
        self.assertTrue(expected.issubset(tokenize_instruction("Open wireless")))
        starlink_tokens = tokenize_instruction("Open StarLink")
        self.assertIn("starlink", starlink_tokens)
        self.assertNotIn("bookmark", starlink_tokens)
        self.assertNotIn("favorite", starlink_tokens)
        self.assertTrue(expected.issubset(tokenize_control("Wi-Fi")))
        self.assertTrue(expected.issubset(tokenize_control("Wifi")))
        network_tokens = tokenize_control("Network StarLink\nInternet access")
        self.assertTrue(
            {"internet", "network", "starlink", "wifi"}.issubset(network_tokens)
        )
        self.assertNotIn("bookmark", network_tokens)
        self.assertNotIn("favorite", network_tokens)

    def test_onedrive_phrase_tokens_are_specific(self) -> None:
        from help_intents import tokenize_control, tokenize_instruction

        control_tokens = tokenize_control("OneDrive - Personal\r\nBacked up and synced")
        instruction_tokens = tokenize_instruction("Open OneDrive")

        self.assertIn("onedrive", control_tokens)
        self.assertIn("onedrive", instruction_tokens)
        self.assertNotIn("one", control_tokens)
        self.assertNotIn("one", instruction_tokens)
        self.assertNotIn("drive", control_tokens)
        self.assertNotIn("drive", instruction_tokens)

    def test_github_phrase_stays_compact_for_url_destination_matching(self) -> None:
        from help_intents import tokenize_instruction

        tokens = tokenize_instruction("Open GitHub bookmark")
        self.assertIn("github", tokens)
        self.assertNotIn("git", tokens)
        self.assertNotIn("hub", tokens)

    def test_all_bookmarks_control_does_not_match_bare_all(self) -> None:
        from help_intents import tokenize_control, tokenize_instruction

        control_tokens = tokenize_control("All Bookmarks")

        self.assertIn("bookmarks", control_tokens)
        self.assertNotIn("all", control_tokens)
        self.assertIn("all", tokenize_instruction("Open all"))
        self.assertTrue({"all", "bookmarks"}.issubset(tokenize_instruction("Open all bookmarks")))

    def test_browser_profile_all_hint_does_not_match_bare_all(self) -> None:
        from help_intents import tokenize_control, tokenize_instruction

        control_tokens = tokenize_control("Abel (All)")

        self.assertIn("abel", control_tokens)
        self.assertNotIn("all", control_tokens)
        self.assertIn("all", tokenize_instruction("Open all"))

    def test_weather_widget_status_does_not_expand_to_clear_action(self) -> None:
        from help_intents import tokenize_control, tokenize_instruction

        widget_tokens = tokenize_control("Widgets 64\u00b0F Clear")
        weather_tokens = tokenize_control("Weather 64\u00b0F Clear")

        self.assertTrue({"weather", "widgets"}.issubset(widget_tokens))
        self.assertIn("weather", weather_tokens)
        self.assertIn("weather", tokenize_instruction("Open weather"))
        self.assertNotIn("clear", widget_tokens)
        self.assertNotIn("x", widget_tokens)
        self.assertNotIn("clear", weather_tokens)
        self.assertNotIn("x", weather_tokens)
        self.assertIn("clear", tokenize_control("Clear"))

    def test_browser_group_phrases_do_not_force_tab_or_input_intents(self) -> None:
        from help_intents import instruction_control_intents, tokenize_instruction

        tab_group_tokens = tokenize_instruction("Open tab groups")
        self.assertIn("groups", tab_group_tokens)
        self.assertNotIn("tab", tab_group_tokens)
        self.assertNotIn("tabitem", instruction_control_intents("Open tab groups"))

        agentic_tokens = tokenize_instruction("Open AgenticField group")
        self.assertTrue({"agentic", "agenticfield"}.issubset(agentic_tokens))
        self.assertNotIn("field", agentic_tokens)
        self.assertFalse(instruction_control_intents("Open AgenticField group"))

    def test_b2b_phrase_does_not_expand_to_bold_formatting(self) -> None:
        from help_intents import tokenize_control, tokenize_instruction

        control_tokens = tokenize_control("B2B group - Closed")
        instruction_tokens = tokenize_instruction("Open B2B group")

        self.assertTrue({"b2", "b2b"}.issubset(control_tokens))
        self.assertTrue({"b2", "b2b"}.issubset(instruction_tokens))
        self.assertNotIn("b", control_tokens)
        self.assertNotIn("bold", control_tokens)
        self.assertNotIn("bold", instruction_tokens)

    def test_compound_taskbar_app_names_do_not_leak_generic_words(self) -> None:
        from help_intents import instruction_control_intents, tokenize_control, tokenize_instruction

        trading_tokens = tokenize_control("TradingView pinned")
        trading_instruction = tokenize_instruction("Open TradingView")
        phone_tokens = tokenize_control("Phone Link pinned")
        phone_instruction = tokenize_instruction("Open phone link")

        self.assertTrue({"trading", "tradingview"}.issubset(trading_tokens))
        self.assertTrue({"trading", "tradingview"}.issubset(trading_instruction))
        self.assertNotIn("view", trading_tokens)
        self.assertNotIn("view", trading_instruction)

        self.assertTrue({"phone", "phone_link"}.issubset(phone_tokens))
        self.assertTrue({"phone", "phone_link"}.issubset(phone_instruction))
        self.assertNotIn("link", phone_tokens)
        self.assertNotIn("link", phone_instruction)
        self.assertNotIn("hyperlink", instruction_control_intents("Open phone link"))

    def test_tab_search_and_windows_search_phrases_are_specific(self) -> None:
        from help_intents import (
            instruction_control_intents,
            tokenize_control,
            tokenize_instruction,
        )

        self.assertEqual(tokenize_instruction("Search tabs"), {"tab_search"})
        self.assertEqual(tokenize_instruction("Open tab search"), {"tab_search"})
        self.assertEqual(tokenize_instruction("Open Windows search"), {"windows_search"})
        self.assertEqual(tokenize_instruction("Search Windows"), {"windows_search"})
        search_tabs_tokens = tokenize_control("Search tabs")
        self.assertEqual(search_tabs_tokens, {"tab_search"})
        self.assertNotIn("find", search_tabs_tokens)
        self.assertNotIn("search", search_tabs_tokens)
        self.assertNotIn("tabs", search_tabs_tokens)
        self.assertIn("tabitem", instruction_control_intents("Show tabs"))
        self.assertIn("tabitem", instruction_control_intents("Highlight tabs"))
        self.assertNotIn("tabitem", instruction_control_intents("Open tab search"))
        self.assertNotIn("windows_search", tokenize_control("Search tabs"))

    def test_close_tab_intent_targets_close_button_not_tabitem(self) -> None:
        from help_intents import instruction_control_intents, tokenize_instruction

        tokens = tokenize_instruction("Close tab.")
        intents = instruction_control_intents("Close tab.")

        self.assertTrue({"close", "dismiss"}.issubset(tokens))
        self.assertIn("button", intents)
        self.assertIn("splitbutton", intents)
        self.assertNotIn("tabitem", intents)

    def test_print_action_aliases_expand_to_printer_language(self) -> None:
        from help_intents import tokenize_instruction, tokenize_control

        self.assertIn("printer", tokenize_instruction("Print document"))
        self.assertIn("print", tokenize_instruction("Open printer"))
        self.assertIn("printer", tokenize_control("Print"))
        self.assertIn("print", tokenize_control("Printer"))

    def test_folder_action_aliases_expand_to_directory_language(self) -> None:
        from help_intents import tokenize_instruction, tokenize_control

        self.assertIn("directory", tokenize_instruction("Open folder"))
        self.assertIn("folder", tokenize_instruction("Open directory"))
        self.assertIn("folder", tokenize_instruction("Open directories"))
        self.assertIn("directory", tokenize_control("Folder"))
        self.assertIn("folder", tokenize_control("Directory"))

    def test_save_action_aliases_expand_to_floppy_language(self) -> None:
        from help_intents import tokenize_instruction, tokenize_control

        self.assertIn("floppy", tokenize_instruction("Save document"))
        self.assertIn("save", tokenize_instruction("Click the floppy disk"))
        self.assertIn("save", tokenize_control("Floppy disk"))
        self.assertIn("floppy", tokenize_control("Save"))

    def test_favorite_action_aliases_expand_to_star_language(self) -> None:
        from help_intents import instruction_control_intents, tokenize_instruction, tokenize_control

        favorite_tokens = tokenize_instruction("Favorite this item")
        bookmark_tokens = tokenize_instruction("Bookmark this item")
        star_tokens = tokenize_control("Star")

        self.assertTrue({"bookmark", "favorite", "star"}.issubset(favorite_tokens))
        self.assertTrue({"bookmark", "favorite", "star"}.issubset(bookmark_tokens))
        self.assertTrue({"bookmark", "favorite", "star"}.issubset(star_tokens))
        bookmark_tab_intents = instruction_control_intents("Bookmark this tab")
        self.assertIn("button", bookmark_tab_intents)
        self.assertIn("splitbutton", bookmark_tab_intents)
        self.assertNotIn("tabitem", bookmark_tab_intents)

    def test_notification_action_aliases_expand_to_bell_language(self) -> None:
        from help_intents import tokenize_instruction, tokenize_control

        bell_tokens = tokenize_instruction("Click the bell")
        notifications_tokens = tokenize_instruction("Open notifications")
        alerts_tokens = tokenize_instruction("Open alerts")
        control_tokens = tokenize_control("Bell")

        expected = {"alerts", "bell", "notification", "notifications"}
        self.assertTrue(expected.issubset(bell_tokens))
        self.assertTrue(expected.issubset(notifications_tokens))
        self.assertTrue(expected.issubset(alerts_tokens))
        self.assertTrue(expected.issubset(control_tokens))

    def test_system_tray_aliases_expand_without_notification_bell_collision(self) -> None:
        from help_intents import tokenize_control, tokenize_instruction

        tray_expected = {"system_tray", "tray"}
        notification_area_expected = {"notification_area", "system_tray", "tray"}

        self.assertTrue(tray_expected.issubset(tokenize_instruction("Open system tray")))
        self.assertTrue(
            notification_area_expected.issubset(
                tokenize_instruction("Open notification area")
            )
        )
        self.assertTrue(
            notification_area_expected.issubset(tokenize_control("Show Hidden Icons"))
        )
        self.assertTrue(
            notification_area_expected.issubset(tokenize_control("Hidden Icons"))
        )
        self.assertNotIn("show", tokenize_instruction("Show history"))
        self.assertNotIn("bell", tokenize_instruction("Open notification area"))
        self.assertNotIn("notifications", tokenize_instruction("Open notification area"))
        self.assertNotIn("tray", tokenize_instruction("Open notifications"))

    def test_info_aliases_expand_to_about_and_details_language(self) -> None:
        from help_intents import tokenize_control, tokenize_instruction

        expected = {"about", "details", "info", "information"}

        self.assertTrue(expected.issubset(tokenize_instruction("Show info")))
        self.assertTrue(expected.issubset(tokenize_instruction("Open information")))
        self.assertTrue(expected.issubset(tokenize_instruction("Open about")))
        self.assertTrue(expected.issubset(tokenize_instruction("Show details")))
        self.assertTrue(expected.issubset(tokenize_control("Info")))
        self.assertTrue(expected.issubset(tokenize_control("Information")))
        self.assertTrue(expected.issubset(tokenize_control("Details")))
        for icon in ("\u2139", "\u24d8", "\U0001f6c8"):
            with self.subTest(icon=icon):
                self.assertTrue(expected.issubset(tokenize_control(icon)))
        self.assertNotIn("info", tokenize_control("i"))

    def test_pin_aliases_expand_to_pushpin_language(self) -> None:
        from help_intents import tokenize_control, tokenize_instruction

        expected = {"pin", "pinned", "pushpin", "thumbtack"}

        self.assertTrue(expected.issubset(tokenize_instruction("Pin this item")))
        self.assertTrue(expected.issubset(tokenize_instruction("Pin to top")))
        self.assertTrue(expected.issubset(tokenize_instruction("Click the pushpin")))
        self.assertTrue(expected.issubset(tokenize_instruction("Click the thumbtack")))
        self.assertTrue(expected.issubset(tokenize_instruction("Unpin this item")))
        self.assertTrue(expected.issubset(tokenize_control("Pushpin")))
        self.assertTrue(expected.issubset(tokenize_control("Thumbtack")))
        self.assertTrue(expected.issubset(tokenize_control("\U0001f4cc")))
        self.assertTrue(expected.issubset(tokenize_control("\U0001f588")))
        self.assertNotIn("location", tokenize_control("\U0001f4cc"))

    def test_mail_aliases_expand_to_envelope_language(self) -> None:
        from help_intents import tokenize_control, tokenize_instruction

        expected = {"email", "envelope", "mail"}

        self.assertTrue(expected.issubset(tokenize_instruction("Open email")))
        self.assertTrue(expected.issubset(tokenize_instruction("Open mail")))
        self.assertTrue(expected.issubset(tokenize_control("Envelope")))
        self.assertTrue(expected.issubset(tokenize_control("Email")))
        self.assertTrue(expected.issubset(tokenize_control("Mail")))
        self.assertTrue({"email", "mail"}.issubset(tokenize_control("Gmail")))
        self.assertTrue({"email", "mail"}.issubset(tokenize_control("Inbox")))
        self.assertTrue(
            {"email", "inbox", "mail"}.issubset(tokenize_control("Recibidos"))
        )
        for icon in ("\u2709", "\U0001f4e7", "\U0001f4e8", "\U0001f4e9"):
            with self.subTest(icon=icon):
                self.assertTrue(expected.issubset(tokenize_control(icon)))
        self.assertNotIn("clipboard", tokenize_control("\u2709"))
        gmail_instruction_tokens = tokenize_instruction("Open Gmail")
        self.assertIn("gmail", gmail_instruction_tokens)
        self.assertNotIn("email", gmail_instruction_tokens)
        self.assertNotIn("mail", gmail_instruction_tokens)

    def test_profile_aliases_expand_to_person_language(self) -> None:
        from help_intents import tokenize_control, tokenize_instruction

        expected = {"account", "person", "profile", "user"}

        self.assertTrue(expected.issubset(tokenize_instruction("Open profile")))
        self.assertTrue(expected.issubset(tokenize_instruction("Open account")))
        self.assertTrue(expected.issubset(tokenize_instruction("Open user menu")))
        self.assertTrue(expected.issubset(tokenize_control("Person")))
        self.assertTrue(expected.issubset(tokenize_control("People")))
        self.assertTrue(expected.issubset(tokenize_control("\U0001f464")))
        self.assertTrue(expected.issubset(tokenize_control("\U0001f465")))

    def test_navigation_arrow_aliases_expand_to_directional_language(self) -> None:
        from help_intents import tokenize_control, tokenize_instruction

        self.assertTrue({"back", "previous"}.issubset(tokenize_instruction("Go back")))
        self.assertTrue({"forward", "next"}.issubset(tokenize_instruction("Go forward")))
        self.assertTrue(
            {"back", "left_arrow", "previous"}.issubset(
                tokenize_instruction("Click left arrow")
            )
        )
        self.assertTrue(
            {"forward", "next", "right_arrow"}.issubset(
                tokenize_instruction("Click right arrow")
            )
        )
        self.assertNotIn("expand", tokenize_instruction("Click left arrow"))
        self.assertNotIn("expand", tokenize_instruction("Click right arrow"))
        for icon in ("\u2190", "\u2039", "<"):
            with self.subTest(icon=icon):
                self.assertTrue({"back", "previous"}.issubset(tokenize_control(icon)))
        for icon in ("\u2192", "\u203a", ">"):
            with self.subTest(icon=icon):
                self.assertTrue({"forward", "next"}.issubset(tokenize_control(icon)))
        self.assertNotIn("undo", tokenize_control("\u2190"))
        self.assertNotIn("redo", tokenize_control("\u2192"))

    def test_symbol_only_control_text_yields_semantic_tokens(self) -> None:
        from help_intents import tokens_from_text

        cases = (
            ("?", {"help", "mark", "question"}),
            ("+", {"add", "create", "new", "plus", "zoom_in"}),
            ("-", {"minimize", "minus", "zoom_out"}),
            ("<", {"arrow", "back", "chevron", "left", "previous"}),
            (">", {"arrow", "chevron", "forward", "next", "right"}),
            ("...", {"dot", "dots", "ellipsis", "menu", "more", "options"}),
            ("\u22ee", {"dot", "dots", "kebab", "menu", "more", "options"}),
            ("\u00d7", {"clear", "close", "dismiss", "x"}),
            ("\u2039", {"arrow", "back", "chevron", "left", "previous"}),
            ("\u203a", {"arrow", "chevron", "forward", "next", "right"}),
            ("\u2190", {"back", "left", "left_arrow", "previous"}),
            ("\u2192", {"forward", "next", "right", "right_arrow"}),
            ("\u2212", {"minimize", "minus", "zoom_out"}),
            ("\u2303", {"arrow", "caret", "chevron", "collapse", "disclosure"}),
            ("\u2304", {"arrow", "caret", "chevron", "collapse", "disclosure"}),
            ("\u24d8", {"about", "details", "info", "information"}),
            ("\u25a1", {"maximize", "square"}),
            ("\u25a2", {"maximize", "square"}),
            ("\u25b4", {"arrow", "caret", "chevron", "collapse", "disclosure"}),
            ("\u25b5", {"arrow", "caret", "chevron", "collapse", "disclosure"}),
            ("\u25b8", {"arrow", "caret", "chevron", "disclosure", "expand"}),
            ("\u25b9", {"arrow", "caret", "chevron", "disclosure", "expand"}),
            ("\u25be", {"arrow", "caret", "chevron", "collapse", "disclosure"}),
            ("\u25bf", {"arrow", "caret", "chevron", "collapse", "disclosure"}),
            ("\u2b1c", {"maximize", "square"}),
            ("\u2699", {"cog", "gear", "options", "preferences", "settings"}),
            ("\u2139", {"about", "details", "info", "information"}),
            ("\u27f2", {"refresh", "reload"}),
            ("\u27f3", {"refresh", "reload"}),
            ("\u2606", {"bookmark", "favorite", "star"}),
            ("\u2665", {"favorite", "heart"}),
            ("\u25b6", {"play"}),
            ("\u23f8", {"pause"}),
            ("\u23f9", {"stop"}),
            ("\u23fa", {"record"}),
            ("\u270f", {"edit", "pencil"}),
            ("\u2702", {"cut", "scissors"}),
            ("\u2709", {"email", "envelope", "mail"}),
            ("\U0001f464", {"account", "avatar", "person", "profile", "user"}),
            ("\U0001f465", {"account", "avatar", "people", "person", "profile", "user"}),
            ("\U0001f517", {"link", "share"}),
            ("\U0001f503", {"refresh", "reload"}),
            ("\U0001f504", {"refresh", "reload"}),
            ("\U0001f514", {"alerts", "bell", "notification", "notifications", "notify"}),
            ("\U0001f3a4", {"mic", "microphone"}),
            ("\U0001f507", {"mute", "speaker", "sound", "volume"}),
            ("\U0001f50a", {"speaker", "sound", "volume"}),
            ("\U0001f4f7", {"camera", "video", "webcam"}),
            ("\U0001f6d2", {"bag", "basket", "cart"}),
            ("\U0001f441", {"eye", "visibility", "visible"}),
            ("\U0001f512", {"lock", "locked", "padlock", "site_info_lock"}),
            ("\U0001f513", {"lock", "padlock", "site_info_lock", "unlock", "unlocked"}),
            ("\U0001f6e1", {"secure", "security", "shield"}),
            ("\U0001f4c5", {"calendar", "date"}),
            ("\U0001f551", {"clock", "time"}),
            ("\U0001f3e0", {"home", "house"}),
            ("\U0001f5a8", {"print", "printer"}),
            ("\U0001f5c4", {"archive", "cabinet", "filing"}),
            ("\U0001f5d1", {"bin", "delete", "remove", "trash", "wastebasket"}),
            ("\U0001f6c8", {"about", "details", "info", "information"}),
            ("\U0001f5d5", {"minimize", "minus"}),
            ("\U0001f5d6", {"maximize", "square"}),
            ("\U0001f5d7", {"overlap", "restore"}),
            ("\U0001f4cb", {"clipboard", "paste"}),
            ("\U0001f4cc", {"pin", "pinned", "pushpin", "thumbtack"}),
            ("\U0001f4e7", {"email", "envelope", "mail"}),
            ("\U0001f4e8", {"email", "envelope", "mail"}),
            ("\U0001f4e9", {"email", "envelope", "mail"}),
            ("\U0001f4ce", {"attach", "attachment", "file", "paperclip"}),
            ("\U0001f587", {"attach", "attachment", "file", "paperclip"}),
            ("\U0001f588", {"pin", "pinned", "pushpin", "thumbtack"}),
            ("\U0001f4c1", {"directory", "folder"}),
            ("\U0001f4be", {"disk", "floppy", "save"}),
            ("\U0001f50d", {"find", "lens", "magnifier", "magnifying", "search"}),
        )
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(tokens_from_text(text), expected)

    def test_picker_and_selector_intents_split_by_context(self) -> None:
        from help_intents import instruction_control_intents

        date_picker_intents = instruction_control_intents("Open the date picker")
        country_selector_intents = instruction_control_intents("Open the country selector")
        country_select_intents = instruction_control_intents("Open Country select.")
        select_yes_intents = instruction_control_intents("Select Yes.")

        self.assertTrue(
            {"button", "splitbutton", "edit", "combobox"}.issubset(date_picker_intents)
        )
        self.assertEqual(country_selector_intents, {"combobox"})
        self.assertIn("combobox", country_select_intents)
        self.assertNotIn("button", country_select_intents)
        self.assertNotIn("radiobutton", country_select_intents)
        self.assertIn("radiobutton", select_yes_intents)
        self.assertNotIn("button", select_yes_intents)

    def test_explicit_radio_and_checkbox_intents_do_not_broaden_to_generic_options(self) -> None:
        from help_intents import instruction_control_intents

        radio_intents = instruction_control_intents("Select Weekly radio.")
        radio_option_intents = instruction_control_intents("Select Weekly radio option.")
        checkbox_option_intents = instruction_control_intents("Select Weekly checkbox option.")

        self.assertEqual(radio_intents, {"radiobutton"})
        self.assertEqual(radio_option_intents, {"radiobutton"})
        self.assertEqual(checkbox_option_intents, {"checkbox"})

    def test_menu_option_intent_does_not_broaden_to_radio_options(self) -> None:
        from help_intents import instruction_control_intents

        from_menu_intents = instruction_control_intents("Select Weekly option from the menu.")
        menu_option_intents = instruction_control_intents("Select Weekly menu option.")

        self.assertIn("menuitem", from_menu_intents)
        self.assertIn("menuitem", menu_option_intents)
        self.assertNotIn("radiobutton", from_menu_intents)
        self.assertNotIn("radiobutton", menu_option_intents)

    def test_iconic_disclosure_and_menu_launcher_intents(self) -> None:
        from help_intents import instruction_control_intents, menu_segment_intent

        chevron_intents = instruction_control_intents("Click the chevron")
        overflow_intents = instruction_control_intents("Open the overflow menu")
        profile_menu_intents = instruction_control_intents("Open the profile menu")
        account_dropdown_intents = instruction_control_intents("Open the account dropdown")
        menu_item_intents = instruction_control_intents("Open the file menu")
        explicit_item_intents = instruction_control_intents("Open the profile menu item")
        launcher_intents = instruction_control_intents("Click Settings launcher.")

        self.assertTrue({"button", "splitbutton"}.issubset(chevron_intents))
        self.assertEqual(overflow_intents, {"button", "splitbutton"})
        self.assertEqual(profile_menu_intents, {"button", "splitbutton"})
        self.assertEqual(launcher_intents, {"button", "splitbutton"})
        self.assertTrue({"button", "splitbutton"}.issubset(account_dropdown_intents))
        self.assertTrue(menu_segment_intent(menu_item_intents))
        self.assertTrue(menu_segment_intent(explicit_item_intents))


class SnapToControlTests(unittest.TestCase):
    def test_snaps_to_nearby_button_with_matching_text(self) -> None:
        from rect_snap import snap_to_control

        button = _make_button("Submit", 100, 200, 60, 30)
        window = _make_window("App", 0, 0, 800, 600, [button])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (75, 195, 70, 35),
            "Click the Submit button",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )
        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 60, 30))
        self.assertIn("Submit", result.matched_text)
        self.assertGreaterEqual(result.confidence, 0.42)

    def test_returns_model_rect_when_no_overlap(self) -> None:
        from rect_snap import snap_to_control

        button = _make_button("Other", 800, 800, 80, 30)
        window = _make_window("App", 700, 700, 200, 200, [button])
        desktop = _FakeDesktop([window])

        model_rect = (50, 50, 80, 30)
        result = snap_to_control(
            model_rect,
            "Click Submit",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )
        self.assertEqual(result.source, "model")
        self.assertEqual(result.rect, model_rect)

    def test_text_match_breaks_ties(self) -> None:
        from rect_snap import snap_to_control

        wrong = _make_button("Cancel", 100, 200, 60, 30)
        right = _make_button("Submit", 110, 200, 60, 30)
        window = _make_window("App", 0, 0, 800, 600, [wrong, right])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (105, 195, 60, 35),
            "Click Submit",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )
        self.assertEqual(result.source, "uia")
        self.assertIn("Submit", result.matched_text)

    def test_skips_non_clickable_control_types(self) -> None:
        from rect_snap import snap_to_control

        text_label = _FakeControl(
            text="Submit",
            control_type="Text",
            rect=_FakeRect(100, 200, 160, 230),
        )
        window = _make_window("App", 0, 0, 800, 600, [text_label])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 200, 60, 30),
            "Click Submit",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )
        self.assertEqual(result.source, "model")

    def test_skips_disabled_or_hidden_controls(self) -> None:
        from rect_snap import snap_to_control

        disabled = _make_button("Submit", 100, 200, 60, 30)
        disabled.element_info.enabled = False
        hidden = _make_button("Submit", 110, 200, 60, 30)
        hidden.element_info.visible = False
        window = _make_window("App", 0, 0, 800, 600, [disabled, hidden])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 200, 60, 30),
            "Click Submit",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )
        self.assertEqual(result.source, "model")

    def test_semantic_mismatch_does_not_snap_wrong_labeled_control(self) -> None:
        from rect_snap import snap_to_control

        cancel = _make_button("Cancel", 100, 200, 60, 30)
        window = _make_window("App", 0, 0, 800, 600, [cancel])
        desktop = _FakeDesktop([window])
        model_rect = (100, 200, 60, 30)

        result = snap_to_control(
            model_rect,
            "Click Submit",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )
        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, model_rect)
        self.assertEqual(result.rejected_reason, "candidate semantic mismatch")

    def test_disclosure_state_mismatch_does_not_snap_opposite_button(self) -> None:
        from rect_snap import snap_to_control

        collapse = _make_button("Collapse Advanced settings", 100, 200, 220, 30)
        window = _make_window("Settings", 0, 0, 800, 600, [collapse])
        desktop = _FakeDesktop([window])
        model_rect = (100, 200, 220, 30)

        result = snap_to_control(
            model_rect,
            "Expand Advanced settings.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, model_rect)
        self.assertEqual(result.rejected_reason, "candidate semantic mismatch")

    def test_start_video_does_not_snap_taskbar_start_button(self) -> None:
        from rect_snap import snap_to_control

        start = _make_button("Start", 0, 560, 55, 40, automation_id="StartButton")
        window = _make_window("Taskbar", 0, 540, 800, 60, [start])
        desktop = _FakeDesktop([window])
        model_rect = (0, 560, 55, 40)

        result = snap_to_control(
            model_rect,
            "Start video.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, model_rect)
        self.assertEqual(result.rejected_reason, "candidate semantic mismatch")

    def test_localized_taskbar_search_status_does_not_snap_minus_alias(self) -> None:
        from rect_snap import snap_to_control

        search = _make_button(
            "搜索 - 世界珊瑚礁日",
            120,
            160,
            220,
            32,
            automation_id="SearchGleamButton",
        )
        window = _make_window("Taskbar", 0, 0, 800, 600, [search])
        desktop = _FakeDesktop([window])
        model_rect = (120, 160, 220, 32)

        for instruction in ("Zoom out.", "Minimize.", "Click minus."):
            with self.subTest(instruction=instruction):
                result = snap_to_control(
                    model_rect,
                    instruction,
                    desktop_factory=lambda: desktop,
                    timeout_ms=2000,
                )

                self.assertEqual(result.source, "uia")
                self.assertEqual(result.rect, model_rect)
                self.assertEqual(result.rejected_reason, "candidate semantic mismatch")

    def test_localized_label_separator_does_not_snap_minus_alias(self) -> None:
        from rect_snap import snap_to_control

        label = _make_button("搜索 - 世界珊瑚礁日", 120, 160, 220, 32)
        window = _make_window("Browser", 0, 0, 800, 600, [label])
        desktop = _FakeDesktop([window])
        model_rect = (120, 160, 220, 32)

        for instruction in ("Zoom out.", "Minimize.", "Click minus."):
            with self.subTest(instruction=instruction):
                result = snap_to_control(
                    model_rect,
                    instruction,
                    desktop_factory=lambda: desktop,
                    timeout_ms=2000,
                )

                self.assertEqual(result.source, "uia")
                self.assertEqual(result.rect, model_rect)
                self.assertEqual(result.rejected_reason, "candidate semantic mismatch")

    def test_generic_view_does_not_snap_taskbar_task_view_button(self) -> None:
        from rect_snap import snap_to_control

        task_view = _make_button("Task View", 100, 560, 90, 40, automation_id="TaskViewButton")
        window = _make_window("Taskbar", 0, 540, 800, 60, [task_view])
        desktop = _FakeDesktop([window])
        model_rect = (100, 560, 90, 40)

        for instruction in ("Open view.", "Open task."):
            with self.subTest(instruction=instruction):
                result = snap_to_control(
                    model_rect,
                    instruction,
                    desktop_factory=lambda: desktop,
                    timeout_ms=2000,
                )

                self.assertEqual(result.source, "uia")
                self.assertEqual(result.rect, model_rect)
                self.assertEqual(result.rejected_reason, "candidate semantic mismatch")

    def test_task_view_phrase_still_snaps_taskbar_task_view_button(self) -> None:
        from rect_snap import snap_to_control

        task_view = _make_button("Task View", 100, 560, 90, 40, automation_id="TaskViewButton")
        window = _make_window("Taskbar", 0, 540, 800, 60, [task_view])
        desktop = _FakeDesktop([window])
        model_rect = (100, 560, 90, 40)

        result = snap_to_control(
            model_rect,
            "Open Task View.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, model_rect)
        self.assertFalse(result.rejected_reason)

    def test_bare_hidden_does_not_snap_taskbar_hidden_icons_button(self) -> None:
        from rect_snap import snap_to_control

        hidden_icons = _make_button("Show Hidden Icons", 100, 560, 90, 40)
        window = _make_window("Taskbar", 0, 540, 800, 60, [hidden_icons])
        desktop = _FakeDesktop([window])
        model_rect = (100, 560, 90, 40)

        for instruction in ("Open hidden.", "Open icons."):
            with self.subTest(instruction=instruction):
                result = snap_to_control(
                    model_rect,
                    instruction,
                    desktop_factory=lambda: desktop,
                    timeout_ms=2000,
                )

                self.assertEqual(result.source, "uia")
                self.assertEqual(result.rect, model_rect)
                self.assertEqual(result.rejected_reason, "candidate semantic mismatch")

    def test_file_picker_action_does_not_snap_taskbar_file_explorer_button(self) -> None:
        from rect_snap import snap_to_control

        file_explorer = _make_button("File Explorer pinned", 120, 160, 180, 32)
        window = _make_window("Taskbar", 0, 140, 800, 80, [file_explorer])
        desktop = _FakeDesktop([window])
        model_rect = (120, 160, 180, 32)

        for instruction in (
            "Open the file picker.",
            "Attach file.",
            "Upload a file.",
            "Choose a file.",
        ):
            with self.subTest(instruction=instruction):
                result = snap_to_control(
                    model_rect,
                    instruction,
                    desktop_factory=lambda: desktop,
                    timeout_ms=2000,
                )

                self.assertEqual(result.source, "uia")
                self.assertEqual(result.rect, model_rect)
                self.assertEqual(result.rejected_reason, "candidate semantic mismatch")

    def test_file_explorer_wording_still_snaps_taskbar_file_explorer_button(self) -> None:
        from rect_snap import snap_to_control

        file_explorer = _make_button("File Explorer pinned", 120, 160, 180, 32)
        window = _make_window("Taskbar", 0, 140, 800, 80, [file_explorer])
        desktop = _FakeDesktop([window])
        model_rect = (120, 160, 180, 32)

        for instruction in ("Open File Explorer.", "Click File Explorer."):
            with self.subTest(instruction=instruction):
                result = snap_to_control(
                    model_rect,
                    instruction,
                    desktop_factory=lambda: desktop,
                    timeout_ms=2000,
                )

                self.assertEqual(result.source, "uia")
                self.assertEqual(result.rect, model_rect)
                self.assertFalse(result.rejected_reason)

    def test_generic_new_does_not_snap_browser_new_tab_button(self) -> None:
        from rect_snap import snap_to_control

        for window_title in ("GitHub - Google Chrome", "Vidbox - Brave"):
            new_tab = _make_button("New Tab", 100, 20, 32, 32)
            window = _make_window(window_title, 0, 0, 800, 600, [new_tab])
            desktop = _FakeDesktop([window])
            model_rect = (100, 20, 32, 32)

            for instruction in ("Open new.", "Create new.", "Add new."):
                with self.subTest(instruction=instruction, window_title=window_title):
                    result = snap_to_control(
                        model_rect,
                        instruction,
                        desktop_factory=lambda: desktop,
                        timeout_ms=2000,
                    )

                    self.assertEqual(result.source, "uia")
                    self.assertEqual(result.rect, model_rect)
                    self.assertEqual(result.rejected_reason, "candidate semantic mismatch")

    def test_external_and_new_window_wording_does_not_snap_browser_new_tab_button(self) -> None:
        from rect_snap import snap_to_control

        new_tab = _make_button("New Tab", 100, 20, 32, 32)
        window = _make_window("GitHub - Google Chrome", 0, 0, 800, 600, [new_tab])
        desktop = _FakeDesktop([window])
        model_rect = (100, 20, 32, 32)

        cases = (
            "Open external.",
            "Open external link.",
            "Open in new window.",
        )
        for instruction in cases:
            with self.subTest(instruction=instruction):
                result = snap_to_control(
                    model_rect,
                    instruction,
                    desktop_factory=lambda: desktop,
                    timeout_ms=2000,
                )

                self.assertEqual(result.source, "uia")
                self.assertEqual(result.rect, model_rect)
                self.assertEqual(result.rejected_reason, "candidate semantic mismatch")

    def test_brave_site_information_generic_view_does_not_snap(self) -> None:
        from rect_snap import snap_to_control

        site_info = _make_button("View site information", 100, 20, 160, 32)
        window = _make_window("Vidbox - Brave", 0, 0, 800, 600, [site_info])
        desktop = _FakeDesktop([window])
        model_rect = (100, 20, 160, 32)

        for instruction in ("Open view.", "Click view."):
            with self.subTest(instruction=instruction):
                result = snap_to_control(
                    model_rect,
                    instruction,
                    desktop_factory=lambda: desktop,
                    timeout_ms=2000,
                )

                self.assertEqual(result.source, "uia")
                self.assertEqual(result.rect, model_rect)
                self.assertEqual(result.rejected_reason, "candidate semantic mismatch")

    def test_new_tab_phrase_still_snaps_browser_new_tab_button(self) -> None:
        from rect_snap import snap_to_control

        for window_title in ("GitHub - Google Chrome", "Vidbox - Brave"):
            with self.subTest(window_title=window_title):
                new_tab = _make_button("New Tab", 100, 20, 32, 32)
                window = _make_window(window_title, 0, 0, 800, 600, [new_tab])
                desktop = _FakeDesktop([window])
                model_rect = (100, 20, 32, 32)

                result = snap_to_control(
                    model_rect,
                    "Open new tab.",
                    desktop_factory=lambda: desktop,
                    timeout_ms=2000,
                )

                self.assertEqual(result.source, "uia")
                self.assertEqual(result.rect, model_rect)
                self.assertFalse(result.rejected_reason)

    def test_pin_state_wording_does_not_snap_opposite_pin_button(self) -> None:
        from rect_snap import snap_to_control

        cases = (
            ("Unpin this item.", "Pin"),
            ("Pin this item.", "Unpin"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                button = _make_button(label, 100, 20, 100, 32)
                window = _make_window("Notes", 0, 0, 800, 600, [button])
                desktop = _FakeDesktop([window])
                model_rect = (100, 20, 100, 32)

                result = snap_to_control(
                    model_rect,
                    instruction,
                    desktop_factory=lambda: desktop,
                    timeout_ms=2000,
                )

                self.assertEqual(result.source, "uia")
                self.assertEqual(result.rect, model_rect)
                self.assertEqual(result.rejected_reason, "candidate semantic mismatch")

    def test_extension_status_words_do_not_snap_access_button(self) -> None:
        from rect_snap import snap_to_control

        extension = _make_button("Codex\nHas access to this site", 100, 20, 120, 32)
        window = _make_window("GitHub - Google Chrome", 0, 0, 800, 600, [extension])
        desktop = _FakeDesktop([window])
        model_rect = (100, 20, 120, 32)

        for instruction in ("Open has.", "Click has."):
            with self.subTest(instruction=instruction):
                result = snap_to_control(
                    model_rect,
                    instruction,
                    desktop_factory=lambda: desktop,
                    timeout_ms=2000,
                )

                self.assertEqual(result.source, "uia")
                self.assertEqual(result.rect, model_rect)
                self.assertEqual(result.rejected_reason, "candidate semantic mismatch")

    def test_named_extension_still_snaps_access_button(self) -> None:
        from rect_snap import snap_to_control

        extension = _make_button("Codex\nHas access to this site", 100, 20, 120, 32)
        window = _make_window("GitHub - Google Chrome", 0, 0, 800, 600, [extension])
        desktop = _FakeDesktop([window])
        model_rect = (100, 20, 120, 32)

        result = snap_to_control(
            model_rect,
            "Open Codex.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, model_rect)
        self.assertFalse(result.rejected_reason)

    def test_bare_desktop_does_not_snap_taskbar_show_desktop_button(self) -> None:
        from rect_snap import snap_to_control

        show_desktop = _make_button("Show Desktop", 100, 560, 12, 40)
        window = _make_window("Taskbar", 0, 540, 800, 60, [show_desktop])
        desktop = _FakeDesktop([window])
        model_rect = (100, 560, 12, 40)

        for instruction in ("Open desktop.", "Click desktop."):
            with self.subTest(instruction=instruction):
                result = snap_to_control(
                    model_rect,
                    instruction,
                    desktop_factory=lambda: desktop,
                    timeout_ms=2000,
                )

                self.assertEqual(result.source, "uia")
                self.assertEqual(result.rect, model_rect)
                self.assertEqual(result.rejected_reason, "candidate semantic mismatch")

    def test_show_desktop_phrase_still_snaps_taskbar_show_desktop_button(self) -> None:
        from rect_snap import snap_to_control

        show_desktop = _make_button("Show Desktop", 100, 560, 12, 40)
        window = _make_window("Taskbar", 0, 540, 800, 60, [show_desktop])
        desktop = _FakeDesktop([window])
        model_rect = (100, 560, 12, 40)

        for instruction in ("Show desktop.", "Minimize all windows.", "Hide all windows."):
            with self.subTest(instruction=instruction):
                result = snap_to_control(
                    model_rect,
                    instruction,
                    desktop_factory=lambda: desktop,
                    timeout_ms=2000,
                )

                self.assertEqual(result.source, "uia")
                self.assertEqual(result.rect, model_rect)
                self.assertFalse(result.rejected_reason)

    def test_program_manager_generic_words_do_not_snap_desktop_icons(self) -> None:
        from rect_snap import snap_to_control

        cases = (
            ("Open desktop.", "Docker Desktop"),
            ("Open about.", "Learn about this picture"),
            ("Create new.", "New Pandora (1)"),
            ("Open app.", "SocialApp"),
            ("Open ai.", "Atlas.ai"),
            ("Open dev.", "Limitles.dev"),
            ("Open source.", "tweetpilot-source"),
            ("Open main.", "awesome-system-prompts-main"),
            ("Open system.", "awesome-system-prompts-main"),
            ("Open installer.", "MinecraftInstaller"),
            ("Open launcher.", "Rockstar Games Launcher"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                item = _make_button(label, 100, 560, 76, 54, control_type="ListItem")
                window = _make_window("Program Manager", 0, 0, 800, 620, [item])
                desktop = _FakeDesktop([window])
                model_rect = (100, 560, 76, 54)

                result = snap_to_control(
                    model_rect,
                    instruction,
                    desktop_factory=lambda: desktop,
                    timeout_ms=2000,
                )

                self.assertEqual(result.source, "uia")
                self.assertEqual(result.rect, model_rect)
                self.assertEqual(result.rejected_reason, "candidate semantic mismatch")

    def test_program_manager_distinctive_words_still_snap_desktop_icons(self) -> None:
        from rect_snap import snap_to_control

        cases = (
            ("Open Docker Desktop.", "Docker Desktop"),
            ("Open this picture.", "Learn about this picture"),
            ("Open Pandora.", "New Pandora (1)"),
            ("Open New Pandora.", "New Pandora (1)"),
            ("Open SocialApp.", "SocialApp"),
            ("Open Atlas.", "Atlas.ai"),
            ("Open Limitles.", "Limitles.dev"),
            ("Open tweetpilot source.", "tweetpilot-source"),
            ("Open awesome prompts.", "awesome-system-prompts-main"),
            ("Open Minecraft installer.", "MinecraftInstaller"),
            ("Open Rockstar launcher.", "Rockstar Games Launcher"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                item = _make_button(label, 100, 560, 76, 54, control_type="ListItem")
                window = _make_window("Program Manager", 0, 0, 800, 620, [item])
                desktop = _FakeDesktop([window])
                model_rect = (100, 560, 76, 54)

                result = snap_to_control(
                    model_rect,
                    instruction,
                    desktop_factory=lambda: desktop,
                    timeout_ms=2000,
                )

                self.assertEqual(result.source, "uia")
                self.assertEqual(result.rect, model_rect)
                self.assertFalse(result.rejected_reason)

    def test_tab_memory_usage_suffix_does_not_snap_as_tab_title(self) -> None:
        from rect_snap import snap_to_control

        cases = (
            ("Open memory.", "Home - Limitless - Stripe - Memory usage - 687 MB"),
            ("Open usage.", "Billing overview - OpenAI API - Memory usage - 99.2 MB"),
            ("Open MB.", "Billing overview - OpenAI API - Memory usage - 99.2 MB"),
            ("Open 99.", "Billing overview - OpenAI API - Memory usage - 99.2 MB"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                tab = _make_button(
                    label,
                    100,
                    20,
                    220,
                    34,
                    control_type="TabItem",
                )
                window = _make_window("GitHub - Google Chrome", 0, 0, 800, 600, [tab])
                desktop = _FakeDesktop([window])
                model_rect = (100, 20, 220, 34)

                result = snap_to_control(
                    model_rect,
                    instruction,
                    desktop_factory=lambda: desktop,
                    timeout_ms=2000,
                )

                self.assertEqual(result.source, "uia")
                self.assertEqual(result.rect, model_rect)
                self.assertEqual(result.rejected_reason, "candidate semantic mismatch")

    def test_about_blank_tab_title_info_wording_does_not_snap(self) -> None:
        from rect_snap import snap_to_control

        about_blank = _make_button("about:blank", 100, 20, 220, 34, control_type="TabItem")
        window = _make_window("about:blank - Google Chrome", 0, 0, 800, 600, [about_blank])
        desktop = _FakeDesktop([window])
        model_rect = (100, 20, 220, 34)

        cases = (
            "Show info.",
            "Open info.",
            "Open details.",
            "Open about.",
            "Show site info.",
            "View site information.",
        )
        for instruction in cases:
            with self.subTest(instruction=instruction):
                result = snap_to_control(
                    model_rect,
                    instruction,
                    desktop_factory=lambda: desktop,
                    timeout_ms=2000,
                )

                self.assertEqual(result.source, "uia")
                self.assertEqual(result.rect, model_rect)
                self.assertEqual(result.rejected_reason, "candidate semantic mismatch")

    def test_about_blank_tab_title_still_snaps_for_explicit_tab_wording(self) -> None:
        from rect_snap import snap_to_control

        about_blank = _make_button("about:blank", 100, 20, 220, 34, control_type="TabItem")
        window = _make_window("about:blank - Google Chrome", 0, 0, 800, 600, [about_blank])
        desktop = _FakeDesktop([window])
        model_rect = (100, 20, 220, 34)

        result = snap_to_control(
            model_rect,
            "Open about:blank tab.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, model_rect)
        self.assertFalse(result.rejected_reason)

    def test_generic_page_section_words_do_not_snap_browser_tab_titles(self) -> None:
        from rect_snap import snap_to_control

        cases = (
            ("Open home.", "Home - Limitless - Stripe - Memory usage - 687 MB"),
            ("Open overview.", "Billing overview - OpenAI API - Memory usage - 195 MB"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                tab = _make_button(label, 100, 20, 220, 34, control_type="TabItem")
                window = _make_window("GitHub - Google Chrome", 0, 0, 800, 600, [tab])
                desktop = _FakeDesktop([window])
                model_rect = (100, 20, 220, 34)

                result = snap_to_control(
                    model_rect,
                    instruction,
                    desktop_factory=lambda: desktop,
                    timeout_ms=2000,
                )

                self.assertEqual(result.source, "uia")
                self.assertEqual(result.rect, model_rect)
                self.assertEqual(result.rejected_reason, "candidate semantic mismatch")

    def test_generic_login_does_not_snap_browser_tab_title(self) -> None:
        from rect_snap import snap_to_control

        tab = _make_button(
            "Log In | Mercury - Memory usage - 372 MB",
            100,
            20,
            220,
            34,
            control_type="TabItem",
        )
        window = _make_window("GitHub - Google Chrome", 0, 0, 800, 600, [tab])
        desktop = _FakeDesktop([window])
        model_rect = (100, 20, 220, 34)

        result = snap_to_control(
            model_rect,
            "Log in.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, model_rect)
        self.assertEqual(result.rejected_reason, "candidate semantic mismatch")

    def test_tab_owner_account_segment_does_not_snap_as_tab_title(self) -> None:
        from rect_snap import snap_to_control

        tab = _make_button(
            "DNS | Records | limitles.dev | "
            "Abelnavarrocarreon@gmail.com's Account | Cloudflare - Memory usage - 580 MB",
            100,
            20,
            220,
            34,
            control_type="TabItem",
        )
        window = _make_window("GitHub - Google Chrome", 0, 0, 800, 600, [tab])
        desktop = _FakeDesktop([window])
        model_rect = (100, 20, 220, 34)

        result = snap_to_control(
            model_rect,
            "Click the Account tab.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, model_rect)
        self.assertEqual(result.rejected_reason, "candidate semantic mismatch")

    def test_profile_request_does_not_snap_plain_browser_identity_controls(self) -> None:
        from rect_snap import snap_to_control

        cases = (
            (
                "Open Chrome profile.",
                "Chrome",
                "Button",
                "about:blank - Google Chrome",
                (120, 160, 48, 32),
            ),
            (
                "Open Chrome account.",
                "Google Chrome - 5 running windows",
                "Button",
                "Taskbar",
                (120, 160, 180, 32),
            ),
        )
        for instruction, label, control_type, window_title, model_rect in cases:
            with self.subTest(instruction=instruction, label=label):
                control = _make_button(
                    label,
                    model_rect[0],
                    model_rect[1],
                    model_rect[2],
                    model_rect[3],
                    control_type=control_type,
                )
                window = _make_window(window_title, 0, 0, 800, 600, [control])
                desktop = _FakeDesktop([window])

                result = snap_to_control(
                    model_rect,
                    instruction,
                    desktop_factory=lambda: desktop,
                    timeout_ms=2000,
                )

                self.assertEqual(result.source, "uia")
                self.assertEqual(result.rect, model_rect)
                self.assertEqual(result.rejected_reason, "candidate semantic mismatch")

    def test_plain_browser_and_edit_profile_still_snap(self) -> None:
        from rect_snap import snap_to_control

        cases = (
            (
                "Open Chrome.",
                "Chrome",
                "about:blank - Google Chrome",
                (120, 160, 48, 32),
            ),
            (
                "Edit profile.",
                "Pencil",
                "about:blank - Google Chrome",
                (120, 160, 90, 32),
            ),
        )
        for instruction, label, window_title, model_rect in cases:
            with self.subTest(instruction=instruction, label=label):
                control = _make_button(
                    label,
                    model_rect[0],
                    model_rect[1],
                    model_rect[2],
                    model_rect[3],
                )
                window = _make_window(window_title, 0, 0, 800, 600, [control])
                desktop = _FakeDesktop([window])

                result = snap_to_control(
                    model_rect,
                    instruction,
                    desktop_factory=lambda: desktop,
                    timeout_ms=2000,
                )

                self.assertEqual(result.source, "uia")
                self.assertEqual(result.rect, model_rect)
                self.assertFalse(result.rejected_reason)

    def test_semantic_mismatch_rejects_loose_model_rect_centered_on_wrong_control(self) -> None:
        from rect_snap import snap_to_control

        cancel = _make_button("Cancel", 100, 200, 60, 30)
        window = _make_window("App", 0, 0, 800, 600, [cancel])
        desktop = _FakeDesktop([window])
        model_rect = (80, 185, 120, 70)

        result = snap_to_control(
            model_rect,
            "Click Save",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 60, 30))
        self.assertEqual(result.rejected_reason, "candidate semantic mismatch")

    def test_snap_rejects_visible_text_conflict_despite_matching_automation_id(self) -> None:
        from rect_snap import snap_to_control

        cancel = _make_button("Cancel", 100, 200, 60, 30, automation_id="saveButton")
        window = _make_window("App", 0, 0, 800, 600, [cancel])
        desktop = _FakeDesktop([window])
        model_rect = (100, 200, 60, 30)

        result = snap_to_control(
            model_rect,
            "Click Save",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, model_rect)
        self.assertEqual(result.rejected_reason, "candidate semantic mismatch")
        self.assertIn("saveButton", result.matched_text)

    def test_snap_prefers_visible_text_over_automation_only_match(self) -> None:
        from rect_snap import snap_to_control

        icon = _make_button("", 100, 200, 32, 32, automation_id="save_button")
        save = _make_button("Save", 145, 200, 80, 32)
        window = _make_window("App", 0, 0, 800, 600, [icon, save])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 200, 32, 32),
            "Click Save.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (145, 200, 80, 32))
        self.assertEqual(result.matched_text, "Save")
        self.assertFalse(result.rejected_reason)

    def test_snap_uses_camel_case_automation_id_when_no_visible_text_exists(self) -> None:
        from rect_snap import snap_to_control

        icon = _make_button("", 100, 200, 32, 32, automation_id="saveButton")
        window = _make_window("Editor", 0, 0, 800, 600, [icon])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 200, 32, 32),
            "Click Save.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 32, 32))
        self.assertFalse(result.rejected_reason)

    def test_snap_rejects_automation_only_match_when_visible_alternative_is_weak(self) -> None:
        from rect_snap import snap_to_control

        icon = _make_button("", 100, 200, 32, 32, automation_id="save_button")
        save = _make_button("Save", 190, 260, 80, 32, control_type="Spinner")
        window = _make_window("App", 0, 0, 800, 600, [icon, save])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 200, 32, 32),
            "Click Save.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 32, 32))
        self.assertEqual(result.rejected_reason, "automation-only target ambiguous")

    def test_snap_accepts_generic_checkbox_label_without_type_text(self) -> None:
        from rect_snap import snap_to_control

        checkbox = _make_button(
            "Enable precision mode",
            100,
            200,
            180,
            32,
            control_type="CheckBox",
        )
        window = _make_window("Settings", 0, 0, 800, 600, [checkbox])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 200, 180, 32),
            "Click this checkbox.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 180, 32))
        self.assertFalse(result.rejected_reason)

    def test_snap_contained_checkbox_requires_child_identity_evidence(self) -> None:
        from rect_snap import snap_to_control

        row = _make_button("Billing row", 10, 10, 600, 80, control_type="ListItem")
        checkbox = _make_button("Done", 24, 34, 20, 20, control_type="CheckBox")
        window = _make_window("Tasks", 0, 0, 800, 600, [row, checkbox])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (10, 10, 600, 80),
            "Click the Archive checkbox in Billing row.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (10, 10, 600, 80))
        self.assertEqual(result.rejected_reason, "control type mismatch")

    def test_snap_accepts_generic_toggle_label_without_type_text(self) -> None:
        from rect_snap import snap_to_control

        checkbox = _make_button(
            "Dark mode",
            100,
            200,
            180,
            32,
            control_type="CheckBox",
        )
        window = _make_window("Settings", 0, 0, 800, 600, [checkbox])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 200, 180, 32),
            "Click this toggle.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 180, 32))
        self.assertFalse(result.rejected_reason)

    def test_snap_accepts_generic_switch_label_without_type_text(self) -> None:
        from rect_snap import snap_to_control

        checkbox = _make_button(
            "Dark mode",
            100,
            200,
            180,
            32,
            control_type="CheckBox",
        )
        window = _make_window("Settings", 0, 0, 800, 600, [checkbox])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 200, 180, 32),
            "Click this switch.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 180, 32))
        self.assertFalse(result.rejected_reason)

    def test_snap_accepts_generic_radio_option_without_label_match(self) -> None:
        from rect_snap import snap_to_control

        option = _make_button(
            "Weekly",
            100,
            200,
            140,
            32,
            control_type="RadioButton",
        )
        window = _make_window("Schedule", 0, 0, 800, 600, [option])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 200, 140, 32),
            "Select this option.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 140, 32))
        self.assertFalse(result.rejected_reason)

    def test_snap_accepts_generic_slider_without_label_match(self) -> None:
        from rect_snap import snap_to_control

        slider = _make_button(
            "Volume",
            100,
            200,
            220,
            32,
            control_type="Slider",
        )
        window = _make_window("Settings", 0, 0, 800, 600, [slider])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 200, 220, 32),
            "Adjust this slider.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 220, 32))
        self.assertFalse(result.rejected_reason)

    def test_snap_accepts_generic_spinner_without_label_match(self) -> None:
        from rect_snap import snap_to_control

        spinner = _make_button(
            "History max tokens",
            100,
            200,
            160,
            32,
            control_type="Spinner",
        )
        window = _make_window("Settings", 0, 0, 800, 600, [spinner])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 200, 160, 32),
            "Adjust this spinner.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 160, 32))
        self.assertFalse(result.rejected_reason)

    def test_snap_accepts_generic_hyperlink_without_label_match(self) -> None:
        from rect_snap import snap_to_control

        link = _make_button(
            "Documentation",
            100,
            200,
            140,
            28,
            control_type="Hyperlink",
        )
        window = _make_window("Help", 0, 0, 800, 600, [link])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 200, 140, 28),
            "Click this hyperlink.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 140, 28))
        self.assertFalse(result.rejected_reason)

    def test_snap_accepts_generic_list_item_without_label_match(self) -> None:
        from rect_snap import snap_to_control

        item = _make_button(
            "Settings",
            100,
            200,
            160,
            32,
            control_type="ListItem",
        )
        window = _make_window("App", 0, 0, 800, 600, [item])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 200, 160, 32),
            "Click this list item.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 160, 32))
        self.assertFalse(result.rejected_reason)

    def test_snap_accepts_generic_tree_item_without_label_match(self) -> None:
        from rect_snap import snap_to_control

        item = _make_button(
            "Settings",
            100,
            200,
            160,
            32,
            control_type="TreeItem",
        )
        window = _make_window("App", 0, 0, 800, 600, [item])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 200, 160, 32),
            "Click this tree item.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 160, 32))
        self.assertFalse(result.rejected_reason)

    def test_snap_accepts_compact_control_type_words_without_label_match(self) -> None:
        from rect_snap import snap_to_control

        cases = (
            ("listitem", "ListItem", (100, 200, 160, 32)),
            ("treeitem", "TreeItem", (100, 200, 160, 32)),
            ("menuitem", "MenuItem", (100, 200, 160, 28)),
            ("tabitem", "TabItem", (100, 200, 140, 32)),
            ("headeritem", "HeaderItem", (100, 200, 140, 28)),
            ("splitbutton", "SplitButton", (100, 200, 160, 32)),
        )
        for word, control_type, rect in cases:
            with self.subTest(word=word):
                item = _make_button(
                    "Settings",
                    rect[0],
                    rect[1],
                    rect[2],
                    rect[3],
                    control_type=control_type,
                )
                window = _make_window("App", 0, 0, 800, 600, [item])
                desktop = _FakeDesktop([window])

                result = snap_to_control(
                    rect,
                    f"Click this {word}.",
                    desktop_factory=lambda: desktop,
                    timeout_ms=2000,
                )

                self.assertEqual(result.source, "uia")
                self.assertEqual(result.rect, rect)
                self.assertFalse(result.rejected_reason)

    def test_snap_accepts_generic_split_button_without_label_match(self) -> None:
        from rect_snap import snap_to_control

        split = _make_button(
            "Export",
            100,
            200,
            160,
            32,
            control_type="SplitButton",
        )
        window = _make_window("App", 0, 0, 800, 600, [split])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 200, 160, 32),
            "Click this split button.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 160, 32))
        self.assertFalse(result.rejected_reason)

    def test_snap_rejects_split_button_intent_on_plain_button(self) -> None:
        from rect_snap import snap_to_control

        button = _make_button(
            "Export",
            100,
            200,
            160,
            32,
            control_type="Button",
        )
        window = _make_window("App", 0, 0, 800, 600, [button])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 200, 160, 32),
            "Click this split button.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 160, 32))
        self.assertEqual(result.rejected_reason, "control type mismatch")

    def test_snap_accepts_browser_address_bar_wording(self) -> None:
        from rect_snap import snap_to_control

        cases = (
            "Focus the URL bar.",
            "Click the location bar.",
            "Click the omnibox.",
        )
        for instruction in cases:
            with self.subTest(instruction=instruction):
                edit = _make_button(
                    "Address",
                    100,
                    200,
                    240,
                    32,
                    control_type="Edit",
                )
                window = _make_window("Browser", 0, 0, 800, 600, [edit])
                desktop = _FakeDesktop([window])

                result = snap_to_control(
                    (100, 200, 240, 32),
                    instruction,
                    desktop_factory=lambda: desktop,
                    timeout_ms=2000,
                )

                self.assertEqual(result.source, "uia")
                self.assertEqual(result.rect, (100, 200, 240, 32))
                self.assertFalse(result.rejected_reason)

    def test_snap_rejects_browser_address_bar_wording_on_plain_button(self) -> None:
        from rect_snap import snap_to_control

        button = _make_button(
            "Address",
            100,
            200,
            240,
            32,
            control_type="Button",
        )
        window = _make_window("Browser", 0, 0, 800, 600, [button])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 200, 240, 32),
            "Focus the URL bar.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 240, 32))
        self.assertEqual(result.rejected_reason, "control type mismatch")

    def test_snap_rejects_text_entry_wording_on_plain_button(self) -> None:
        from rect_snap import snap_to_control

        cases = (
            ("Type your email.", "Email", (100, 200, 180, 32)),
            ("Enter the verification code.", "Verification code", (100, 200, 220, 32)),
            ("Click the search bar.", "Search", (100, 200, 180, 32)),
            ("Click the filter bar.", "Filter", (100, 200, 180, 32)),
        )
        for instruction, label, rect in cases:
            with self.subTest(instruction=instruction):
                button = _make_button(label, *rect, control_type="Button")
                window = _make_window("App", 0, 0, 800, 600, [button])
                desktop = _FakeDesktop([window])

                result = snap_to_control(
                    rect,
                    instruction,
                    desktop_factory=lambda: desktop,
                    timeout_ms=2000,
                )

                self.assertEqual(result.source, "uia")
                self.assertEqual(result.rect, rect)
                self.assertEqual(result.rejected_reason, "control type mismatch")

    def test_clear_search_snap_accepts_x_inside_search_field(self) -> None:
        from rect_snap import snap_to_control

        search = _make_button("Search", 120, 160, 500, 40, control_type="Edit")
        clear = _make_button("\u00d7", 586, 166, 28, 28)
        window = _make_window("App", 0, 0, 800, 600, [search, clear])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (586, 166, 28, 28),
            "Clear search.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (586, 166, 28, 28))
        self.assertFalse(result.rejected_reason)

    def test_clear_search_snap_rejects_window_close_x_button(self) -> None:
        from rect_snap import snap_to_control

        close = _make_button("\u00d7", 700, 20, 32, 32, automation_id="Close")
        window = _make_window("Dialog", 0, 0, 800, 600, [close])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (700, 20, 32, 32),
            "Clear search.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (700, 20, 32, 32))
        self.assertEqual(result.rejected_reason, "candidate semantic mismatch")

    def test_password_visibility_wording_does_not_snap_opposite_state_button(self) -> None:
        from rect_snap import snap_to_control

        cases = (
            ("Hide password.", "Show password"),
            ("Show password.", "Hide password"),
            ("Conceal password.", "Reveal password"),
            ("Reveal password.", "Conceal password"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                button = _make_button(label, 360, 160, 120, 32)
                field = _make_button("Password", 120, 160, 220, 32, control_type="Edit")
                window = _make_window("Login", 0, 0, 800, 600, [field, button])
                desktop = _FakeDesktop([window])

                result = snap_to_control(
                    (360, 160, 120, 32),
                    instruction,
                    desktop_factory=lambda: desktop,
                    timeout_ms=2000,
                )

                self.assertEqual(result.source, "uia")
                self.assertEqual(result.rect, (360, 160, 120, 32))
                self.assertEqual(result.rejected_reason, "candidate semantic mismatch")

    def test_audio_volume_direction_does_not_snap_opposite_button(self) -> None:
        from rect_snap import snap_to_control

        cases = (
            ("Increase volume.", "Decrease volume"),
            ("Decrease volume.", "Increase volume"),
            ("Volume up.", "Volume down"),
            ("Lower volume.", "Raise volume"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                button = _make_button(label, 360, 160, 140, 32)
                window = _make_window("Sound", 0, 0, 800, 600, [button])
                desktop = _FakeDesktop([window])

                result = snap_to_control(
                    (360, 160, 140, 32),
                    instruction,
                    desktop_factory=lambda: desktop,
                    timeout_ms=2000,
                )

                self.assertEqual(result.source, "uia")
                self.assertEqual(result.rect, (360, 160, 140, 32))
                self.assertEqual(result.rejected_reason, "candidate semantic mismatch")

    def test_action_family_mismatch_does_not_snap_generic_overlap_button(self) -> None:
        from rect_snap import snap_to_control

        cases = (
            ("Click Send message.", "Delete message"),
            ("Delete message.", "Send message"),
            ("Save document.", "Delete document"),
            ("Download file.", "Upload file"),
            ("Open file.", "Save file"),
            ("Open file.", "Export file"),
            ("Open file.", "Import file"),
            ("Remove member.", "Add member"),
            ("Add member.", "Remove member"),
            ("Approve request.", "Reject request"),
            ("Reject request.", "Approve request"),
            ("Accept invite.", "Decline invite"),
            ("Decline invite.", "Accept invite"),
            ("Allow access.", "Deny access"),
            ("Deny access.", "Allow access"),
            ("Sort by name.", "Filter by name"),
            ("Filter by name.", "Sort by name"),
            ("Refresh feed.", "Reload page"),
            ("Reload page.", "Refresh feed"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                button = _make_button(label, 360, 160, 160, 32)
                window = _make_window("App", 0, 0, 800, 600, [button])
                desktop = _FakeDesktop([window])

                result = snap_to_control(
                    (360, 160, 160, 32),
                    instruction,
                    desktop_factory=lambda: desktop,
                    timeout_ms=2000,
                )

                self.assertEqual(result.source, "uia")
                self.assertEqual(result.rect, (360, 160, 160, 32))
                self.assertEqual(result.rejected_reason, "candidate semantic mismatch")

    def test_explicit_action_context_mismatch_does_not_snap_shared_alias_button(self) -> None:
        from rect_snap import snap_to_control

        cases = (
            ("Edit profile.", "View profile", "App"),
            ("Apply changes.", "Cancel changes", "App"),
            ("Cancel changes.", "Apply changes", "App"),
            ("Apply filter.", "Apply coupon", "App"),
            ("Sort A to Z.", "Z to A", "App"),
            ("Sort Z to A.", "A to Z", "App"),
            ("Search users.", "Filter users", "App"),
            ("Filter users.", "Search users", "App"),
            ("Download file.", "Export", "App"),
            ("Export file.", "Download", "App"),
            ("Import file.", "Upload", "App"),
            ("Upload file.", "Import", "App"),
            ("Copy link.", "Duplicate link", "App"),
            ("Copy selected text.", "Duplicate text", "App"),
            ("Open new tab.", "New window", "GitHub - Google Chrome"),
            ("Open new window.", "New tab", "GitHub - Google Chrome"),
            ("Favorite this item.", "Bookmark this tab", "GitHub - Google Chrome"),
            ("Open account.", "Delete account", "App"),
            ("Open account.", "Archive account", "App"),
            ("Open account.", "Create account", "App"),
            ("Open report.", "Download report", "App"),
            ("Show invoice.", "Delete invoice", "App"),
            ("View invoice.", "Pay invoice", "App"),
            ("View project.", "Archive project", "App"),
            ("Open message.", "Send message", "App"),
            ("Display account.", "Delete account", "App"),
            ("Inspect report.", "Download report", "App"),
            ("Read invoice.", "Pay invoice", "App"),
            ("Review project.", "Share project", "App"),
            ("See message.", "Send message", "App"),
            ("Click account.", "Delete account", "App"),
            ("Tap account.", "Archive account", "App"),
            ("Go to report.", "Download report", "App"),
            ("Click message.", "Send message", "App"),
            ("Click invoice.", "Pay invoice", "App"),
            ("Click project.", "Share project", "App"),
            ("Find account.", "Delete account", "App"),
            ("Search account.", "Archive account", "App"),
            ("Search for account.", "Delete account", "App"),
            ("Look for report.", "Download report", "App"),
            ("Focus account.", "Delete account", "App"),
            ("Delete account.", "Delete message", "App"),
            ("Download report.", "Download invoice", "App"),
            ("Copy coupon.", "Copy address", "App"),
            ("Send invite.", "Send email", "App"),
            ("Save document.", "Save profile", "App"),
            ("Save document.", "Document saved", "App"),
            ("Send message.", "Message sent", "App"),
            ("Cancel subscription.", "Subscription cancelled", "App"),
            ("Upload file.", "File uploaded", "App"),
            ("Download report.", "Report downloaded", "App"),
            ("Save card.", "Save profile", "App"),
            ("Delete section.", "Delete account", "App"),
            ("Archive card.", "Archive email", "App"),
            ("Delete account.", "Delete", "Messages"),
            ("Show sidebar.", "Hide sidebar", "App"),
            ("Hide sidebar.", "Show sidebar", "App"),
            ("Open details.", "Close details", "App"),
            ("Close details.", "Open details", "App"),
            ("Open details.", "Edit details", "App"),
            ("Open profile.", "Edit profile", "App"),
            ("Mute speaker.", "Unmuted speaker", "App"),
            ("Mute speaker.", "Muted speaker", "App"),
            ("Show sidebar.", "Visible sidebar", "App"),
            ("Expand Advanced settings.", "Collapsed Advanced settings", "App"),
            ("Expand Advanced settings.", "Expanded Advanced settings", "App"),
            ("Lock account.", "Unlocked account", "App"),
            ("Lock account.", "Locked account", "App"),
            ("Archive email.", "Unarchived email", "App"),
            ("Archive email.", "Archived email", "App"),
            ("Start recording.", "Stopped recording", "App"),
            ("Start recording.", "Started recording", "App"),
            ("Mute microphone.", "Unmute microphone", "App"),
            ("Unmute microphone.", "Mute microphone", "App"),
            ("Lock account.", "Unlock account", "App"),
            ("Unlock account.", "Lock account", "App"),
            ("Archive email.", "Unarchive email", "App"),
            ("Unarchive email.", "Archive email", "App"),
            ("Subscribe channel.", "Unsubscribe channel", "App"),
            ("Unsubscribe channel.", "Subscribe channel", "App"),
            ("Connect account.", "Disconnect account", "App"),
            ("Disconnect account.", "Connect account", "App"),
            ("Activate notifications.", "Deactivate notifications", "App"),
            ("Deactivate notifications.", "Activate notifications", "App"),
            ("Check in guest.", "Check out guest", "Hotel PMS"),
            ("Check out guest.", "Check in guest", "Hotel PMS"),
            ("Start recording.", "Stop recording", "App"),
            ("Stop recording.", "Start recording", "App"),
        )
        for instruction, label, window_title in cases:
            with self.subTest(instruction=instruction, label=label):
                button = _make_button(label, 360, 160, 180, 32)
                window = _make_window(window_title, 0, 0, 800, 600, [button])
                desktop = _FakeDesktop([window])

                result = snap_to_control(
                    (360, 160, 180, 32),
                    instruction,
                    desktop_factory=lambda: desktop,
                    timeout_ms=2000,
                )

                self.assertEqual(result.source, "uia")
                self.assertEqual(result.rect, (360, 160, 180, 32))
                self.assertEqual(result.rejected_reason, "candidate semantic mismatch")

    def test_sidebar_item_does_not_snap_browser_tabitem(self) -> None:
        from rect_snap import snap_to_control

        tab = _make_button(
            "Settings - MyApp - Google Chrome",
            20,
            10,
            220,
            32,
            control_type="TabItem",
        )
        window = _make_window("MyApp - Google Chrome", 0, 0, 800, 600, [tab])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (20, 10, 220, 32),
            "Click the Settings sidebar item.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (20, 10, 220, 32))
        self.assertEqual(result.rejected_reason, "candidate semantic mismatch")

    def test_remove_formatting_does_not_snap_delete_family_button(self) -> None:
        from rect_snap import snap_to_control

        for label in ("Delete", "Trash", "Remove"):
            with self.subTest(label=label):
                button = _make_button(label, 360, 160, 120, 32)
                window = _make_window("Editor", 0, 0, 800, 600, [button])
                desktop = _FakeDesktop([window])

                result = snap_to_control(
                    (360, 160, 120, 32),
                    "Remove formatting.",
                    desktop_factory=lambda: desktop,
                    timeout_ms=2000,
                )

                self.assertEqual(result.source, "uia")
                self.assertEqual(result.rect, (360, 160, 120, 32))
                self.assertEqual(result.rejected_reason, "candidate semantic mismatch")

    def test_history_action_does_not_snap_opposite_button(self) -> None:
        from rect_snap import snap_to_control

        cases = (
            ("Undo change.", "Redo change"),
            ("Redo change.", "Undo change"),
            ("Undo last change.", "Redo last change"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                button = _make_button(label, 360, 160, 150, 32)
                window = _make_window("Editor", 0, 0, 800, 600, [button])
                desktop = _FakeDesktop([window])

                result = snap_to_control(
                    (360, 160, 150, 32),
                    instruction,
                    desktop_factory=lambda: desktop,
                    timeout_ms=2000,
                )

                self.assertEqual(result.source, "uia")
                self.assertEqual(result.rect, (360, 160, 150, 32))
                self.assertEqual(result.rejected_reason, "candidate semantic mismatch")

    def test_checkbox_state_action_does_not_snap_opposite_control(self) -> None:
        from rect_snap import snap_to_control

        cases = (
            ("Enable notifications.", "Disable notifications"),
            ("Disable notifications.", "Enable notifications"),
            ("Enable notifications.", "Turn off notifications"),
            ("Turn on notifications.", "Disable notifications"),
            ("Check Remember me.", "Uncheck Remember me"),
            ("Uncheck Remember me.", "Check Remember me"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                checkbox = _make_button(label, 360, 160, 180, 32, control_type="CheckBox")
                window = _make_window("Settings", 0, 0, 800, 600, [checkbox])
                desktop = _FakeDesktop([window])

                result = snap_to_control(
                    (360, 160, 180, 32),
                    instruction,
                    desktop_factory=lambda: desktop,
                    timeout_ms=2000,
                )

                self.assertEqual(result.source, "uia")
                self.assertEqual(result.rect, (360, 160, 180, 32))
                self.assertEqual(result.rejected_reason, "candidate semantic mismatch")

    def test_state_action_button_with_matching_label_can_snap(self) -> None:
        from rect_snap import snap_to_control

        button = _make_button("Enable notifications", 360, 160, 180, 32)
        checkbox = _make_button(
            "Notifications",
            120,
            160,
            180,
            32,
            control_type="CheckBox",
        )
        window = _make_window("Settings", 0, 0, 800, 600, [button, checkbox])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (360, 160, 180, 32),
            "Enable notifications.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (360, 160, 180, 32))
        self.assertFalse(result.rejected_reason)

    def test_navigation_direction_does_not_snap_media_transport_button(self) -> None:
        from rect_snap import snap_to_control

        cases = (
            ("Go forward.", "Next track"),
            ("Go forward.", "Next song"),
            ("Go back.", "Previous track"),
            ("Go back.", "Previous song"),
            ("Go back.", "Back up and sync"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                button = _make_button(label, 360, 160, 140, 32)
                window = _make_window("Player", 0, 0, 800, 600, [button])
                desktop = _FakeDesktop([window])

                result = snap_to_control(
                    (360, 160, 140, 32),
                    instruction,
                    desktop_factory=lambda: desktop,
                    timeout_ms=2000,
                )

                self.assertEqual(result.source, "uia")
                self.assertEqual(result.rect, (360, 160, 140, 32))
                self.assertEqual(result.rejected_reason, "candidate semantic mismatch")

    def test_profile_page_does_not_snap_browser_chrome_profile_button(self) -> None:
        from rect_snap import snap_to_control

        button = _make_button("Profile 1", 700, 80, 40, 36)
        window = _make_window("about:blank - Google Chrome", 0, 0, 900, 700, [button])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (700, 80, 40, 36),
            "Open profile page.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (700, 80, 40, 36))
        self.assertEqual(result.rejected_reason, "candidate semantic mismatch")

    def test_app_local_wording_does_not_snap_moved_browser_chrome(self) -> None:
        from rect_snap import snap_to_control

        cases = (
            (
                "Refresh the dashboard widget.",
                _make_button("Reload", 96, 108, 34, 34),
                "Dashboard - Google Chrome",
                (96, 108, 34, 34),
            ),
            (
                "Minimize the panel in the app.",
                _make_button("Minimize", 910, 100, 30, 30, automation_id="Minimize"),
                "Dashboard - Google Chrome",
                (910, 100, 30, 30),
            ),
            (
                "Open site info in the app.",
                _make_button("site_info_lock", 90, 8, 28, 34),
                "",
                (90, 8, 28, 34),
            ),
            (
                "Open Collections in the app.",
                _make_button("Collections", 904, 8, 42, 34, automation_id="Collections"),
                "CRM - Microsoft Edge",
                (904, 8, 42, 34),
            ),
            (
                "Open Reading list in the app.",
                _make_button("Reading list", 904, 8, 42, 34, automation_id="ReadingList"),
                "CRM - Microsoft Edge",
                (904, 8, 42, 34),
            ),
            (
                "Open Copilot in the app.",
                _make_button("Copilot", 904, 8, 42, 34, automation_id="Copilot"),
                "CRM - Microsoft Edge",
                (904, 8, 42, 34),
            ),
            (
                "Open Passwords in the app.",
                _make_button("Passwords", 904, 8, 42, 34, automation_id="Passwords"),
                "CRM - Microsoft Edge",
                (904, 8, 42, 34),
            ),
            (
                "Open Browser essentials in the app sidebar.",
                _make_button(
                    "Browser essentials",
                    900,
                    8,
                    80,
                    34,
                    automation_id="browseressentials",
                ),
                "Dashboard - Google Chrome",
                (900, 8, 80, 34),
            ),
            (
                "Open New tab in the app.",
                _make_button("New tab", 904, 8, 42, 34),
                "CRM - Microsoft Edge",
                (904, 8, 42, 34),
            ),
            (
                "Open Search tabs in the app.",
                _make_button("Search tabs", 904, 8, 82, 34),
                "CRM - Microsoft Edge",
                (904, 8, 82, 34),
            ),
        )
        for instruction, button, window_title, rect in cases:
            with self.subTest(instruction=instruction):
                window = _make_window(window_title, 0, 0, 1200, 800, [button])
                desktop = _FakeDesktop([window])

                result = snap_to_control(
                    rect,
                    instruction,
                    desktop_factory=lambda: desktop,
                    timeout_ms=2000,
                )

                self.assertEqual(result.source, "uia")
                self.assertEqual(result.rect, rect)
                self.assertEqual(result.rejected_reason, "candidate semantic mismatch")

    def test_snap_keeps_explicit_enter_button_as_button(self) -> None:
        from rect_snap import snap_to_control

        button = _make_button("Enter", 100, 200, 120, 32)
        window = _make_window("Dialog", 0, 0, 800, 600, [button])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 200, 120, 32),
            "Click the Enter button.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 120, 32))
        self.assertFalse(result.rejected_reason)

    def test_snap_rejects_state_and_choice_wording_on_plain_button(self) -> None:
        from rect_snap import snap_to_control

        cases = (
            ("Check Remember me.", "Remember me", (100, 200, 180, 32)),
            ("Uncheck Remember me.", "Remember me", (100, 200, 180, 32)),
            ("Tick Remember me.", "Remember me", (100, 200, 180, 32)),
            ("Turn on dark mode.", "Dark mode", (100, 200, 180, 32)),
            ("Enable notifications.", "Notifications", (100, 200, 180, 32)),
            ("Pick Daily choice.", "Daily", (100, 200, 180, 32)),
            ("Choose Weekly option.", "Weekly", (100, 200, 180, 32)),
            ("Select Yes.", "Yes", (100, 200, 180, 32)),
            ("Open Country select.", "Country", (100, 200, 180, 32)),
        )
        for instruction, label, rect in cases:
            with self.subTest(instruction=instruction):
                button = _make_button(label, *rect, control_type="Button")
                window = _make_window("App", 0, 0, 800, 600, [button])
                desktop = _FakeDesktop([window])

                result = snap_to_control(
                    rect,
                    instruction,
                    desktop_factory=lambda: desktop,
                    timeout_ms=2000,
                )

                self.assertEqual(result.source, "uia")
                self.assertEqual(result.rect, rect)
                self.assertEqual(result.rejected_reason, "control type mismatch")

    def test_snap_keeps_check_for_updates_button(self) -> None:
        from rect_snap import snap_to_control

        button = _make_button("Check for updates", 100, 200, 180, 32)
        window = _make_window("Settings", 0, 0, 800, 600, [button])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 200, 180, 32),
            "Check for updates.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 180, 32))
        self.assertFalse(result.rejected_reason)

    def test_snap_accepts_generic_button_control_suffix(self) -> None:
        from rect_snap import snap_to_control

        button = _make_button("Submit", 100, 200, 120, 32)
        window = _make_window("App", 0, 0, 800, 600, [button])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 200, 120, 32),
            "Click this button control.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 120, 32))
        self.assertFalse(result.rejected_reason)

    def test_snap_accepts_literal_edit_wording(self) -> None:
        from rect_snap import snap_to_control

        edit = _make_button(
            "Search",
            100,
            200,
            240,
            32,
            control_type="Edit",
        )
        window = _make_window("App", 0, 0, 800, 600, [edit])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 200, 240, 32),
            "Click this edit control.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 240, 32))
        self.assertFalse(result.rejected_reason)

    def test_snap_accepts_contextual_control_container_wording(self) -> None:
        from rect_snap import snap_to_control

        cases = (
            ("Click this toolbar button.", "Save", "Button", (100, 200, 100, 32)),
            ("Click Toolbar button.", "Toolbar", "Button", (100, 200, 100, 32)),
            ("Click this toolbar icon.", "Settings", "Button", (100, 200, 32, 32)),
            ("Click this form field.", "Name", "Edit", (100, 200, 240, 32)),
            ("Click this dialog button.", "OK", "Button", (100, 200, 80, 32)),
            ("Click this modal button.", "OK", "Button", (100, 200, 80, 32)),
            ("Click this panel button.", "Save", "Button", (100, 200, 100, 32)),
            ("Click Panel button.", "Panel", "Button", (100, 200, 100, 32)),
            ("Click this table row.", "Order 123", "ListItem", (100, 200, 240, 32)),
            ("Click this grid row.", "Order 123", "ListItem", (100, 200, 240, 32)),
            ("Click this page link.", "Docs", "Hyperlink", (100, 200, 120, 28)),
            ("Click this card checkbox.", "Done", "CheckBox", (100, 200, 160, 32)),
            ("Click this section toggle.", "Dark mode", "CheckBox", (100, 200, 160, 32)),
            ("Click this drawer item.", "Settings", "ListItem", (100, 200, 160, 32)),
            ("Click this pane button.", "Apply", "Button", (100, 200, 100, 32)),
            ("Click this popup menu item.", "Open", "MenuItem", (100, 200, 120, 28)),
            ("Click this navigation tab.", "Settings", "TabItem", (100, 200, 140, 32)),
            ("Click this sidebar item.", "Settings", "ListItem", (100, 200, 160, 32)),
            ("Click this nav item.", "Settings", "ListItem", (100, 200, 160, 32)),
        )
        for instruction, label, control_type, rect in cases:
            with self.subTest(instruction=instruction):
                control = _make_button(
                    label,
                    rect[0],
                    rect[1],
                    rect[2],
                    rect[3],
                    control_type=control_type,
                )
                window = _make_window("App", 0, 0, 800, 600, [control])
                desktop = _FakeDesktop([window])

                result = snap_to_control(
                    rect,
                    instruction,
                    desktop_factory=lambda: desktop,
                    timeout_ms=2000,
                )

                self.assertEqual(result.source, "uia")
                self.assertEqual(result.rect, rect)
                self.assertFalse(result.rejected_reason)

    def test_snap_button_wording_prefers_tight_search_button_over_field(self) -> None:
        from rect_snap import snap_to_control

        edit = _make_button(
            "Search",
            10,
            10,
            240,
            32,
            control_type="Edit",
        )
        button = _make_button(
            "",
            220,
            12,
            28,
            28,
            automation_id="SearchButton",
        )
        window = _make_window("App", 0, 0, 800, 600, [edit, button])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (220, 12, 28, 28),
            "Click the Search field button.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (220, 12, 28, 28))
        self.assertFalse(result.rejected_reason)

    def test_snap_allows_toggle_sidebar_button_label(self) -> None:
        from rect_snap import snap_to_control

        button = _make_button(
            "Toggle sidebar",
            100,
            200,
            150,
            32,
            control_type="Button",
        )
        checkbox = _make_button(
            "Dark mode",
            100,
            250,
            150,
            32,
            control_type="CheckBox",
        )
        window = _make_window("App", 0, 0, 800, 600, [button, checkbox])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 200, 150, 32),
            "Click Toggle sidebar.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 150, 32))
        self.assertFalse(result.rejected_reason)

    def test_snap_accepts_generic_focus_field_intent_without_label_match(self) -> None:
        from rect_snap import snap_to_control

        edit = _make_button(
            "Search",
            100,
            200,
            200,
            32,
            control_type="Edit",
        )
        window = _make_window("Search", 0, 0, 800, 600, [edit])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 200, 200, 32),
            "Focus this field.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 200, 32))
        self.assertFalse(result.rejected_reason)

    def test_snap_accepts_generic_column_header_intent_without_label_match(self) -> None:
        from rect_snap import snap_to_control

        header = _make_button(
            "Status",
            100,
            50,
            120,
            28,
            control_type="HeaderItem",
        )
        window = _make_window("Grid", 0, 0, 800, 600, [header])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 50, 120, 28),
            "Click this column header.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 50, 120, 28))
        self.assertFalse(result.rejected_reason)

    def test_snap_rejects_checkbox_intent_on_plain_button(self) -> None:
        from rect_snap import snap_to_control

        button = _make_button("", 100, 200, 32, 32)
        window = _make_window("Settings", 0, 0, 800, 600, [button])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 200, 32, 32),
            "Click this checkbox.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 32, 32))
        self.assertEqual(result.rejected_reason, "control type mismatch")

    def test_snap_accepts_deictic_exact_labeled_control(self) -> None:
        from rect_snap import snap_to_control

        button = _make_button("Save", 100, 200, 80, 32)
        window = _make_window("App", 0, 0, 800, 600, [button])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 200, 80, 32),
            "Click here.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 80, 32))
        self.assertFalse(result.rejected_reason)

    def test_snap_uses_single_checkbox_inside_loose_row(self) -> None:
        from rect_snap import snap_to_control

        checkbox = _make_button("Done", 24, 34, 20, 20, control_type="CheckBox")
        row = _make_button("Task row", 10, 10, 600, 80, control_type="ListItem")
        window = _make_window("Tasks", 0, 0, 800, 600, [row, checkbox])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (10, 10, 600, 80),
            "Click this checkbox.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (24, 34, 20, 20))
        self.assertFalse(result.rejected_reason)

    def test_snap_uses_single_checkbox_inside_contextual_row(self) -> None:
        from rect_snap import snap_to_control

        checkbox = _make_button("Done", 24, 34, 20, 20, control_type="CheckBox")
        row = _make_button("Task row", 10, 10, 600, 80, control_type="ListItem")
        window = _make_window("Tasks", 0, 0, 800, 600, [row, checkbox])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (10, 10, 600, 80),
            "Click the checkbox in Task row.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (24, 34, 20, 20))
        self.assertFalse(result.rejected_reason)

    def test_snap_prefers_splitbutton_menu_segment(self) -> None:
        from rect_snap import snap_to_control

        split = _make_button("Export", 100, 100, 180, 32, control_type="SplitButton")
        primary = _make_button("Export", 100, 100, 140, 32, control_type="Button")
        menu = _make_button("Export menu", 240, 100, 40, 32, control_type="MenuItem")
        window = _make_window("Toolbar", 0, 0, 800, 600, [split, primary, menu])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 100, 180, 32),
            "Open the Export menu.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (240, 100, 40, 32))
        self.assertFalse(result.rejected_reason)

    def test_snap_prefers_splitbutton_dropdown_segment(self) -> None:
        from rect_snap import snap_to_control

        split = _make_button("Export", 100, 100, 180, 32, control_type="SplitButton")
        primary = _make_button("Export", 100, 100, 140, 32, control_type="Button")
        menu = _make_button("Export menu", 240, 100, 40, 32, control_type="MenuItem")
        window = _make_window("Toolbar", 0, 0, 800, 600, [split, primary, menu])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 100, 180, 32),
            "Open the Export drop down.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (240, 100, 40, 32))
        self.assertFalse(result.rejected_reason)

    def test_snap_rejects_splitbutton_menu_without_precise_segment(self) -> None:
        from rect_snap import snap_to_control

        split = _make_button("Export", 100, 100, 180, 32, control_type="SplitButton")
        window = _make_window("Toolbar", 0, 0, 800, 600, [split])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 100, 180, 32),
            "Open the Export menu.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rejected_reason, "compound target ambiguous")

    def test_snap_accepts_menu_launcher_button_wording(self) -> None:
        from rect_snap import snap_to_control

        cases = (
            "Open the overflow menu.",
            "Click the kebab menu.",
            "Click the three dots menu.",
            "Open the More options menu.",
        )
        for instruction in cases:
            with self.subTest(instruction=instruction):
                button = _make_button("More options", 100, 200, 120, 32)
                window = _make_window("App", 0, 0, 800, 600, [button])
                desktop = _FakeDesktop([window])

                result = snap_to_control(
                    (100, 200, 120, 32),
                    instruction,
                    desktop_factory=lambda: desktop,
                    timeout_ms=2000,
                )

                self.assertEqual(result.source, "uia")
                self.assertEqual(result.rect, (100, 200, 120, 32))
                self.assertFalse(result.rejected_reason)

    def test_snap_accepts_common_button_aliases(self) -> None:
        from rect_snap import snap_to_control

        cases = (
            ("Click Confirm.", "OK", (100, 200, 80, 32)),
            ("Click Previous.", "Back", (100, 200, 80, 32)),
            ("Click Continue.", "Next", (100, 200, 80, 32)),
            ("Click Sign in.", "Log in", (100, 200, 100, 32)),
        )
        for instruction, label, rect in cases:
            with self.subTest(instruction=instruction):
                button = _make_button(label, *rect)
                window = _make_window("Dialog", 0, 0, 800, 600, [button])
                desktop = _FakeDesktop([window])

                result = snap_to_control(
                    rect,
                    instruction,
                    desktop_factory=lambda: desktop,
                    timeout_ms=2000,
                )

                self.assertEqual(result.source, "uia")
                self.assertEqual(result.rect, rect)
                self.assertFalse(result.rejected_reason)

    def test_snap_prefers_disclosure_button_inside_broad_row(self) -> None:
        from rect_snap import snap_to_control

        cases = (
            ("Click the chevron.", "Expand"),
            ("Click the down arrow.", "Expand"),
            ("Expand Advanced settings.", "Expand"),
            ("Collapse Advanced settings.", "Collapse"),
        )
        for instruction, button_text in cases:
            with self.subTest(instruction=instruction):
                row = _make_button(
                    "Advanced settings",
                    20,
                    80,
                    500,
                    80,
                    control_type="ListItem",
                )
                button = _make_button(button_text, 480, 104, 28, 28)
                window = _make_window("Settings", 0, 0, 800, 600, [row, button])
                desktop = _FakeDesktop([window])

                result = snap_to_control(
                    (20, 80, 500, 80),
                    instruction,
                    desktop_factory=lambda: desktop,
                    timeout_ms=2000,
                )

                self.assertEqual(result.source, "uia")
                self.assertEqual(result.rect, (480, 104, 28, 28))
                self.assertFalse(result.rejected_reason)

    def test_snap_rejects_selector_wording_on_plain_button(self) -> None:
        from rect_snap import snap_to_control

        cases = (
            "Open the Country selector.",
            "Click the Country picker.",
        )
        for instruction in cases:
            with self.subTest(instruction=instruction):
                button = _make_button("Country", 100, 200, 120, 32)
                window = _make_window("Form", 0, 0, 800, 600, [button])
                desktop = _FakeDesktop([window])

                result = snap_to_control(
                    (100, 200, 120, 32),
                    instruction,
                    desktop_factory=lambda: desktop,
                    timeout_ms=2000,
                )

                self.assertEqual(result.source, "uia")
                self.assertEqual(result.rect, (100, 200, 120, 32))
                self.assertEqual(result.rejected_reason, "control type mismatch")

    def test_snap_keeps_explicit_picker_button(self) -> None:
        from rect_snap import snap_to_control

        button = _make_button("Picker", 100, 200, 120, 32)
        window = _make_window("Form", 0, 0, 800, 600, [button])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 200, 120, 32),
            "Click the picker button.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 120, 32))
        self.assertFalse(result.rejected_reason)

    def test_snap_accepts_contextual_picker_launcher_buttons(self) -> None:
        from rect_snap import snap_to_control

        cases = (
            ("Open the Date picker.", "Date", (100, 200, 120, 32)),
            ("Click the Calendar picker.", "Calendar", (100, 200, 140, 32)),
            ("Open the Color picker.", "Color", (100, 200, 120, 32)),
            ("Click the File picker.", "Choose file", (100, 200, 140, 32)),
        )
        for instruction, label, rect in cases:
            with self.subTest(instruction=instruction):
                button = _make_button(label, *rect)
                window = _make_window("Form", 0, 0, 800, 600, [button])
                desktop = _FakeDesktop([window])

                result = snap_to_control(
                    rect,
                    instruction,
                    desktop_factory=lambda: desktop,
                    timeout_ms=2000,
                )

                self.assertEqual(result.source, "uia")
                self.assertEqual(result.rect, rect)
                self.assertFalse(result.rejected_reason)

    def test_snap_accepts_file_action_alias_buttons(self) -> None:
        from rect_snap import snap_to_control

        cases = (
            ("Upload a file.", "Browse", (100, 200, 120, 32)),
            ("Open the file picker.", "Browse", (100, 200, 120, 32)),
            ("Choose a file.", "Browse", (100, 200, 120, 32)),
            ("Attach a document.", "Choose file", (100, 200, 140, 32)),
        )
        for instruction, label, rect in cases:
            with self.subTest(instruction=instruction):
                button = _make_button(label, *rect)
                window = _make_window("Form", 0, 0, 800, 600, [button])
                desktop = _FakeDesktop([window])

                result = snap_to_control(
                    rect,
                    instruction,
                    desktop_factory=lambda: desktop,
                    timeout_ms=2000,
                )

                self.assertEqual(result.source, "uia")
                self.assertEqual(result.rect, rect)
                self.assertFalse(result.rejected_reason)

    def test_snap_rejects_multiple_checkboxes_inside_loose_row(self) -> None:
        from rect_snap import snap_to_control

        first = _make_button("Done", 24, 24, 20, 20, control_type="CheckBox")
        second = _make_button("Archived", 24, 52, 20, 20, control_type="CheckBox")
        row = _make_button("Task row", 10, 10, 600, 80, control_type="ListItem")
        window = _make_window("Tasks", 0, 0, 800, 600, [row, first, second])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (10, 10, 600, 80),
            "Click this checkbox.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rejected_reason, "control type mismatch")

    def test_snap_rejects_background_duplicate_when_foreground_is_plausible(self) -> None:
        from rect_snap import snap_to_control

        background_save = _make_button("Save", 100, 200, 80, 32)
        foreground_save = _make_button("Save", 100, 250, 80, 32)
        background = _make_window(
            "Background Editor",
            0,
            0,
            400,
            320,
            [background_save],
            handle=101,
        )
        foreground = _make_window(
            "Active Editor",
            0,
            40,
            400,
            320,
            [foreground_save],
            handle=202,
        )
        desktop = _FakeDesktop([background, foreground])

        result = snap_to_control(
            (100, 200, 80, 32),
            "Click Save.",
            desktop_factory=lambda: desktop,
            foreground_handle_provider=lambda: 202,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 80, 32))
        self.assertEqual(result.rejected_reason, "foreground target ambiguous")

    def test_snap_rejects_occluded_background_target(self) -> None:
        from rect_snap import snap_to_control

        save = _make_button("Save", 100, 200, 80, 32)
        background = _make_window("Background Editor", 0, 0, 400, 320, [save], handle=101)
        blocking_dialog = _make_window("Blocking Dialog", 70, 170, 180, 100, [], handle=202)
        desktop = _FakeDesktop([background, blocking_dialog])

        def topmost_at(x: int, y: int) -> int:
            if 70 <= x < 250 and 170 <= y < 270:
                return 202
            return 101

        result = snap_to_control(
            (100, 200, 80, 32),
            "Click Save.",
            desktop_factory=lambda: desktop,
            topmost_handle_provider=topmost_at,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 80, 32))
        self.assertEqual(result.matched_text, "Save")
        self.assertEqual(result.rejected_reason, "occluded target")

    def test_snap_rejects_own_process_target_instead_of_raw_fallback(self) -> None:
        from rect_snap import snap_to_control

        helper_button = _make_button("Save", 100, 200, 60, 30)
        helper_window = _make_window("Helper", 0, 0, 800, 600, [helper_button], handle=101)
        desktop = _FakeDesktop([helper_window])
        model_rect = (100, 200, 60, 30)

        with patch("rect_snap._is_own_process_window", side_effect=lambda hwnd: hwnd == 101):
            result = snap_to_control(
                model_rect,
                "Click Save",
                desktop_factory=lambda: desktop,
                timeout_ms=2000,
            )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, model_rect)
        self.assertEqual(result.matched_text, "Save")
        self.assertEqual(result.rejected_reason, "own process target")

    def test_snap_uses_common_ui_label_synonyms(self) -> None:
        from rect_snap import snap_to_control

        options = _make_button("Options", 100, 200, 60, 30)
        window = _make_window("App", 0, 0, 800, 600, [options])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 200, 60, 30),
            "Click the settings gear.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 60, 30))
        self.assertIn("Options", result.matched_text)

    def test_factory_failure_falls_back_cleanly(self) -> None:
        from rect_snap import snap_to_control

        def boom() -> _FakeDesktop:
            raise RuntimeError("UIA unavailable")

        result = snap_to_control(
            (10, 10, 50, 50),
            "Click X",
            desktop_factory=boom,
            timeout_ms=2000,
        )
        self.assertEqual(result.source, "model")
        self.assertEqual(result.rect, (10, 10, 50, 50))

    def test_no_visible_windows_falls_back(self) -> None:
        from rect_snap import snap_to_control

        desktop = _FakeDesktop([])
        result = snap_to_control(
            (10, 10, 50, 50),
            "Click X",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )
        self.assertEqual(result.source, "model")

    def test_descends_into_window_to_find_button(self) -> None:
        from rect_snap import snap_to_control

        button = _make_button("Save", 410, 510, 50, 24)
        panel = _FakeControl(
            text="Toolbar",
            control_type="Pane",
            rect=_FakeRect(400, 500, 600, 540),
            children=[button],
        )
        window = _make_window("Editor", 0, 0, 1000, 800, [panel])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (405, 505, 60, 30),
            "Save the file",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )
        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (410, 510, 50, 24))


class ControlInventoryTests(unittest.TestCase):
    def _capture(self):
        from screen import Capture

        return Capture(
            png_bytes=b"png",
            width=800,
            height=600,
            monitor_left=0,
            monitor_top=0,
            scale=1.0,
        )

    def test_collects_clickable_visible_controls_with_stable_ids(self) -> None:
        from control_inventory import collect_control_candidates

        save = _make_button("Save", 100, 200, 60, 30, automation_id="save-btn")
        label = _FakeControl(
            text="Save",
            control_type="Text",
            rect=_FakeRect(100, 250, 160, 280),
        )
        disabled = _make_button("Disabled", 200, 200, 80, 30)
        disabled.element_info.enabled = False
        offscreen = _make_button("Offscreen", 900, 200, 80, 30)
        window = _make_window("Editor", 0, 0, 800, 600, [save, label, disabled, offscreen])
        desktop = _FakeDesktop([window])

        candidates = collect_control_candidates(
            self._capture(),
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].id, "c001")
        self.assertEqual(candidates[0].text, "Save")
        self.assertEqual(candidates[0].automation_id, "save-btn")
        self.assertEqual(candidates[0].rect, (100, 200, 60, 30))

    def test_collect_dedupes_same_visible_control_with_different_automation_ids(self) -> None:
        from control_inventory import collect_control_candidates

        save_a = _make_button("Save", 100, 200, 60, 30, automation_id="save-a")
        save_b = _make_button("Save", 100, 200, 60, 30, automation_id="save-b")
        window = _make_window("Editor", 0, 0, 800, 600, [save_a, save_b])
        desktop = _FakeDesktop([window])

        candidates = collect_control_candidates(
            self._capture(),
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].text, "Save")
        self.assertEqual(candidates[0].rect, (100, 200, 60, 30))

    def test_candidate_prompt_includes_target_ids_and_normalized_rects(self) -> None:
        from control_inventory import collect_control_candidates, format_candidates_for_prompt

        button = _make_button("Submit", 80, 120, 40, 30)
        window = _make_window("App", 0, 0, 800, 600, [button])
        desktop = _FakeDesktop([window])
        candidates = collect_control_candidates(
            self._capture(),
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        prompt = format_candidates_for_prompt(candidates, self._capture())

        self.assertIn("c001", prompt)
        self.assertIn("Submit", prompt)
        self.assertIn("norm=(100,200,50,50)", prompt)

    def test_candidate_prompt_separates_visible_text_from_automation_id(self) -> None:
        from control_inventory import ControlCandidate, format_candidates_for_prompt

        prompt = format_candidates_for_prompt(
            [
                ControlCandidate(
                    "c001",
                    "Cancel",
                    "button",
                    (10, 10, 60, 30),
                    automation_id="saveButton",
                )
            ],
            self._capture(),
        )

        self.assertIn('visible_text="Cancel"', prompt)
        self.assertIn('automation_id="saveButton"', prompt)
        self.assertNotIn('"Cancel saveButton"', prompt)
        self.assertIn("do not treat automation_id as visible screen text", prompt)

    def test_resolve_exact_target_id_wins_when_semantically_compatible(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        candidates = [
            ControlCandidate("c001", "Cancel", "button", (10, 10, 60, 30)),
            ControlCandidate("c002", "Submit", "button", (100, 10, 60, 30)),
        ]

        result = resolve_candidate_target(
            target_id="c002",
            instruction="Click Submit.",
            candidates=candidates,
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertEqual(result.rect, (100, 10, 60, 30))
        self.assertFalse(result.rejected_reason)

    def test_target_id_semantic_mismatch_is_rejected(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c002",
            instruction="Click Cancel.",
            candidates=[
                ControlCandidate("c001", "Cancel", "button", (10, 10, 60, 30)),
                ControlCandidate("c002", "Submit", "button", (100, 10, 60, 30)),
            ],
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.rejected_reason, "target_id semantic mismatch")

    def test_target_id_accepts_deictic_exact_labeled_control(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click here.",
            candidates=[
                ControlCandidate("c001", "Save", "button", (100, 100, 80, 32)),
            ],
            model_rect=(100, 100, 80, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (100, 100, 80, 32))

    def test_visible_text_conflict_rejects_target_id_despite_matching_automation_id(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click Save.",
            candidates=[
                ControlCandidate(
                    "c001",
                    "Cancel",
                    "button",
                    (10, 10, 60, 30),
                    automation_id="saveButton",
                )
            ],
            model_rect=(10, 10, 60, 30),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.rejected_reason, "target_id semantic mismatch")

    def test_visible_text_conflict_does_not_resolve_by_automation_id_text_match(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="",
            instruction="Click Save.",
            candidates=[
                ControlCandidate(
                    "c001",
                    "Cancel",
                    "button",
                    (10, 10, 60, 30),
                    automation_id="saveButton",
                )
            ],
        )

        self.assertIsNone(result)

    def test_text_match_prefers_visible_label_over_automation_only_geometry(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="",
            instruction="Click Save.",
            candidates=[
                ControlCandidate("c001", "", "button", (10, 10, 32, 32), automation_id="saveButton"),
                ControlCandidate("c002", "Save", "button", (120, 10, 80, 32)),
            ],
            model_rect=(10, 10, 32, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "text_match")
        self.assertEqual(result.target_id, "c002")
        self.assertFalse(result.rejected_reason)

    def test_target_id_accepts_common_ui_synonym_with_exact_geometry(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click the settings gear.",
            candidates=[
                ControlCandidate("c001", "Options", "button", (100, 10, 32, 32)),
            ],
            model_rect=(100, 10, 32, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (100, 10, 32, 32))

    def test_text_match_uses_common_ui_synonyms(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="",
            instruction="Click the settings gear.",
            candidates=[
                ControlCandidate("c001", "Options", "button", (100, 10, 32, 32)),
            ],
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "text_match")
        self.assertEqual(result.target_id, "c001")

    def test_text_match_contextual_row_prefers_single_checkbox(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="",
            instruction="Click the checkbox in Task row.",
            candidates=[
                ControlCandidate("c001", "Task row", "listitem", (10, 10, 600, 80)),
                ControlCandidate("c002", "Done", "checkbox", (24, 34, 20, 20)),
            ],
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "text_match")
        self.assertEqual(result.target_id, "c002")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (24, 34, 20, 20))

    def test_text_match_contextual_row_rejects_multiple_checkboxes(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="",
            instruction="Click the checkbox in Task row.",
            candidates=[
                ControlCandidate("c001", "Task row", "listitem", (10, 10, 600, 80)),
                ControlCandidate("c002", "Done", "checkbox", (24, 24, 20, 20)),
                ControlCandidate("c003", "Archived", "checkbox", (24, 52, 20, 20)),
            ],
        )

        self.assertIsNone(result)

    def test_unlabeled_target_id_can_pass_with_exact_geometry_when_unambiguous(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click this icon.",
            candidates=[
                ControlCandidate("c001", "", "button", (100, 10, 32, 32)),
            ],
            model_rect=(100, 10, 32, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.source, "target_id")

    def test_unlabeled_target_id_rejects_exact_geometry_when_visible_alternative_matches(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click Save.",
            candidates=[
                ControlCandidate("c001", "", "button", (10, 10, 32, 32)),
                ControlCandidate("c002", "Save", "button", (100, 10, 60, 30)),
            ],
            model_rect=(10, 10, 32, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertEqual(result.rejected_reason, "target_id ambiguous")

    def test_automation_only_target_id_rejects_when_visible_alternative_matches(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click Save.",
            candidates=[
                ControlCandidate("c001", "", "button", (10, 10, 32, 32), automation_id="saveButton"),
                ControlCandidate("c002", "Save", "button", (120, 10, 80, 32)),
            ],
            model_rect=(10, 10, 32, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertEqual(result.rejected_reason, "target_id ambiguous")

    def test_target_id_duplicate_label_without_geometry_is_rejected(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c002",
            instruction="Click Save.",
            candidates=[
                ControlCandidate("c001", "Save", "button", (10, 10, 60, 30)),
                ControlCandidate("c002", "Save", "button", (300, 10, 60, 30)),
            ],
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.rejected_reason, "target_id ambiguous")

    def test_target_id_duplicate_label_with_geometry_is_accepted(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c002",
            instruction="Click Save.",
            candidates=[
                ControlCandidate("c001", "Save", "button", (10, 10, 60, 30)),
                ControlCandidate("c002", "Save", "button", (300, 10, 60, 30)),
            ],
            model_rect=(298, 8, 64, 34),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (300, 10, 60, 30))

    def test_target_id_rejects_matching_row_when_tight_child_action_exists(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click Settings.",
            candidates=[
                ControlCandidate("c001", "Settings", "listitem", (10, 10, 600, 80)),
                ControlCandidate("c002", "Settings", "button", (20, 20, 70, 30)),
            ],
            model_rect=(10, 10, 600, 80),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertEqual(result.rejected_reason, "target_id ambiguous")

    def test_generic_target_id_rejects_row_containing_tight_actions(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click this button.",
            candidates=[
                ControlCandidate("c001", "Account row", "listitem", (10, 10, 600, 80)),
                ControlCandidate("c002", "Edit", "button", (450, 20, 60, 30)),
                ControlCandidate("c003", "Delete", "button", (520, 20, 70, 30)),
            ],
            model_rect=(10, 10, 600, 80),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertEqual(result.rejected_reason, "target_id ambiguous")

    def test_menu_target_id_rejects_broad_splitbutton_with_menu_segment(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Open the Export menu.",
            candidates=[
                ControlCandidate("c001", "Export", "splitbutton", (100, 100, 180, 32)),
                ControlCandidate("c002", "Export", "button", (100, 100, 140, 32)),
                ControlCandidate("c003", "Export menu", "menuitem", (240, 100, 40, 32)),
            ],
            model_rect=(100, 100, 180, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertEqual(result.rejected_reason, "target_id ambiguous")

    def test_text_match_prefers_splitbutton_menu_segment(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="",
            instruction="Open the Export menu.",
            candidates=[
                ControlCandidate("c001", "Export", "splitbutton", (100, 100, 180, 32)),
                ControlCandidate("c002", "Export", "button", (100, 100, 140, 32)),
                ControlCandidate("c003", "Export menu", "menuitem", (240, 100, 40, 32)),
            ],
            model_rect=(100, 100, 180, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "text_match")
        self.assertEqual(result.target_id, "c003")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (240, 100, 40, 32))

    def test_text_match_prefers_splitbutton_dropdown_segment(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="",
            instruction="Open the Export drop down.",
            candidates=[
                ControlCandidate("c001", "Export", "splitbutton", (100, 100, 180, 32)),
                ControlCandidate("c002", "Export", "button", (100, 100, 140, 32)),
                ControlCandidate("c003", "Export menu", "menuitem", (240, 100, 40, 32)),
            ],
            model_rect=(100, 100, 180, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "text_match")
        self.assertEqual(result.target_id, "c003")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (240, 100, 40, 32))

    def test_menu_launcher_target_id_accepts_button(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        cases = (
            "Open the overflow menu.",
            "Click the kebab menu.",
            "Click the three dots menu.",
            "Open the More options menu.",
        )
        for instruction in cases:
            with self.subTest(instruction=instruction):
                result = resolve_candidate_target(
                    target_id="c001",
                    instruction=instruction,
                    candidates=[
                        ControlCandidate("c001", "More options", "button", (10, 10, 120, 32)),
                    ],
                    model_rect=(10, 10, 120, 32),
                )

                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.source, "target_id")
                self.assertEqual(result.target_id, "c001")
                self.assertFalse(result.rejected_reason)
                self.assertEqual(result.rect, (10, 10, 120, 32))

    def test_menu_launcher_text_match_accepts_button(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="",
            instruction="Open the overflow menu.",
            candidates=[
                ControlCandidate("c001", "More options", "button", (10, 10, 120, 32)),
            ],
            model_rect=(10, 10, 120, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "text_match")
        self.assertEqual(result.target_id, "c001")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 120, 32))

    def test_common_button_alias_target_ids_are_accepted(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        cases = (
            ("Click Confirm.", "OK", "ok"),
            ("Apply changes.", "OK", "ok"),
            ("Confirm selection.", "Apply", "apply"),
            ("Click Previous.", "Back", "back"),
            ("Click Continue.", "Next", "next"),
            ("Click Sign in.", "Log in", "login"),
        )
        for instruction, label, candidate_id in cases:
            with self.subTest(instruction=instruction):
                result = resolve_candidate_target(
                    target_id=candidate_id,
                    instruction=instruction,
                    candidates=[
                        ControlCandidate(candidate_id, label, "button", (10, 10, 120, 32)),
                    ],
                    model_rect=(10, 10, 120, 32),
                )

                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.source, "target_id")
                self.assertEqual(result.target_id, candidate_id)
                self.assertFalse(result.rejected_reason)

    def test_disclosure_target_id_inside_broad_row_accepts_button(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        cases = (
            ("Click the chevron.", "Expand", "c002"),
            ("Click the down arrow.", "Expand", "c002"),
            ("Expand Advanced settings.", "Expand", "c002"),
            ("Collapse Advanced settings.", "Collapse", "c003"),
        )
        for instruction, label, candidate_id in cases:
            with self.subTest(instruction=instruction):
                result = resolve_candidate_target(
                    target_id=candidate_id,
                    instruction=instruction,
                    candidates=[
                        ControlCandidate("c001", "Advanced settings", "listitem", (10, 10, 500, 80)),
                        ControlCandidate(candidate_id, label, "button", (468, 36, 28, 28)),
                    ],
                    model_rect=(10, 10, 500, 80),
                )

                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.source, "target_id")
                self.assertEqual(result.target_id, candidate_id)
                self.assertFalse(result.rejected_reason)
                self.assertEqual(result.rect, (468, 36, 28, 28))

    def test_selector_target_id_rejects_plain_button(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c002",
            instruction="Open the Country selector.",
            candidates=[
                ControlCandidate("c001", "Country", "combobox", (10, 10, 220, 32)),
                ControlCandidate("c002", "Country", "button", (280, 10, 100, 32)),
            ],
            model_rect=(280, 10, 100, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertEqual(result.rejected_reason, "target_id control type mismatch")

    def test_selector_text_match_prefers_combobox_over_same_label_button(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        cases = (
            "Open the Country selector.",
            "Click the Country picker.",
        )
        for instruction in cases:
            with self.subTest(instruction=instruction):
                result = resolve_candidate_target(
                    target_id="",
                    instruction=instruction,
                    candidates=[
                        ControlCandidate("c001", "Country", "combobox", (10, 10, 220, 32)),
                        ControlCandidate("c002", "Country", "button", (280, 10, 100, 32)),
                    ],
                    model_rect=(280, 10, 100, 32),
                )

                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.source, "text_match")
                self.assertEqual(result.target_id, "c001")
                self.assertEqual(result.rect, (10, 10, 220, 32))
                self.assertFalse(result.rejected_reason)

    def test_generic_dropdown_broad_rect_with_multiple_comboboxes_stays_ambiguous(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from agent import _parse_live_help_decision
        from help_session import resolve_help_target

        class Capture:
            width = 1000
            height = 1000
            scale = 1.0
            monitor_left = 0
            monitor_top = 0
            image = None

            def to_screen_coords(self, x: int, y: int) -> tuple[int, int]:
                return (x, y)

        candidates = [
            ControlCandidate("country", "Country", "combobox", (10, 10, 200, 32)),
            ControlCandidate("state", "State", "combobox", (10, 50, 200, 32)),
        ]
        instruction = "Open this dropdown."
        broad_rect = (10, 10, 200, 72)

        wrong_target = resolve_candidate_target(
            target_id="state",
            instruction=instruction,
            candidates=candidates,
            model_rect=broad_rect,
        )
        text_target = resolve_candidate_target(
            target_id="",
            instruction=instruction,
            candidates=candidates,
            model_rect=broad_rect,
        )
        snap_target = snap_candidate_target(
            instruction=instruction,
            candidates=candidates,
            model_rect=broad_rect,
        )
        help_target = resolve_help_target(
            _parse_live_help_decision(
                json.dumps({
                    "kind": "step",
                    "instruction": instruction,
                    "target_id": "state",
                    "target": {"x": 10, "y": 10, "width": 200, "height": 72},
                })
            ),
            Capture(),
            candidates,
        )

        self.assertEqual(wrong_target.target_id, "state")
        self.assertEqual(wrong_target.rejected_reason, "target_id ambiguous")
        self.assertEqual(text_target.rejected_reason, "ambiguous text match")
        self.assertEqual(snap_target.rejected_reason, "ambiguous candidate snap")
        self.assertEqual(help_target.rejected_reason, "ambiguous candidate snap")

        named_target = resolve_candidate_target(
            target_id="state",
            instruction="Open State dropdown.",
            candidates=candidates,
            model_rect=broad_rect,
        )
        self.assertEqual(named_target.target_id, "state")
        self.assertFalse(named_target.rejected_reason)

        wrong_named_target = resolve_candidate_target(
            target_id="country",
            instruction="Open State dropdown.",
            candidates=candidates,
            model_rect=(10, 10, 200, 32),
        )
        named_text_target = resolve_candidate_target(
            target_id="",
            instruction="Open State dropdown.",
            candidates=candidates,
            model_rect=(10, 10, 200, 32),
        )
        named_help_target = resolve_help_target(
            _parse_live_help_decision(
                json.dumps({
                    "kind": "step",
                    "instruction": "Open State dropdown.",
                    "target_id": "country",
                    "target": {"x": 10, "y": 10, "width": 200, "height": 32},
                })
            ),
            Capture(),
            candidates,
        )

        self.assertEqual(wrong_named_target.target_id, "country")
        self.assertEqual(wrong_named_target.rejected_reason, "target_id semantic mismatch")
        self.assertEqual(named_text_target.target_id, "state")
        self.assertFalse(named_text_target.rejected_reason)
        self.assertEqual(named_help_target.target_id, "state")
        self.assertFalse(named_help_target.rejected_reason)

    def test_selector_wording_keeps_explicit_picker_button_target_id(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click the picker button.",
            candidates=[
                ControlCandidate("c001", "Picker", "button", (10, 10, 120, 32)),
            ],
            model_rect=(10, 10, 120, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertFalse(result.rejected_reason)

    def test_contextual_picker_launcher_target_ids_accept_buttons(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        cases = (
            ("Open the Date picker.", "Date"),
            ("Click the Calendar picker.", "Calendar"),
            ("Open the Color picker.", "Color"),
            ("Click the File picker.", "Choose file"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction):
                result = resolve_candidate_target(
                    target_id="c001",
                    instruction=instruction,
                    candidates=[
                        ControlCandidate("c001", label, "button", (10, 10, 140, 32)),
                    ],
                    model_rect=(10, 10, 140, 32),
                )

                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.source, "target_id")
                self.assertEqual(result.target_id, "c001")
                self.assertFalse(result.rejected_reason)

    def test_file_action_alias_target_ids_accept_buttons(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        cases = (
            ("Upload a file.", "Browse"),
            ("Open the file picker.", "Browse"),
            ("Choose a file.", "Browse"),
            ("Attach a document.", "Choose file"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction):
                result = resolve_candidate_target(
                    target_id="c001",
                    instruction=instruction,
                    candidates=[
                        ControlCandidate("c001", label, "button", (10, 10, 140, 32)),
                    ],
                    model_rect=(10, 10, 140, 32),
                )

                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.source, "target_id")
                self.assertEqual(result.target_id, "c001")
                self.assertFalse(result.rejected_reason)

    def test_generic_field_target_id_accepts_edit_containing_clear_action(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click this field.",
            candidates=[
                ControlCandidate("c001", "Search", "edit", (10, 10, 600, 40)),
                ControlCandidate("c002", "Clear", "button", (570, 14, 28, 28)),
            ],
            model_rect=(10, 10, 600, 40),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 600, 40))

    def test_generic_field_target_id_rejects_wrong_button_type(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click this field.",
            candidates=[
                ControlCandidate("c001", "Clear", "button", (570, 14, 28, 28)),
            ],
            model_rect=(570, 14, 28, 28),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertEqual(result.rejected_reason, "target_id control type mismatch")

    def test_select_field_rejects_same_label_menuitem_when_field_exists(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target

        candidates = [
            ControlCandidate("combo", "State", "combobox", (100, 100, 220, 32)),
            ControlCandidate("stale", "State", "menuitem", (100, 150, 220, 32)),
        ]

        target_id = resolve_candidate_target(
            target_id="stale",
            instruction="Select the State field.",
            candidates=candidates,
            model_rect=(100, 150, 220, 32),
        )
        text_target = resolve_candidate_target(
            target_id="",
            instruction="Select the State field.",
            candidates=candidates,
            model_rect=(100, 150, 220, 32),
        )
        snap_target = snap_candidate_target(
            instruction="Select the State field.",
            candidates=candidates,
            model_rect=(100, 150, 220, 32),
        )

        self.assertEqual(target_id.target_id, "stale")
        self.assertEqual(target_id.rejected_reason, "target_id control type mismatch")
        self.assertEqual(text_target.target_id, "combo")
        self.assertFalse(text_target.rejected_reason)
        self.assertEqual(snap_target.target_id, "combo")
        self.assertFalse(snap_target.rejected_reason)

    def test_generic_checkbox_target_id_accepts_checkbox_label_without_type_text(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click this checkbox.",
            candidates=[
                ControlCandidate("c001", "Enable precision mode", "checkbox", (10, 10, 200, 32)),
            ],
            model_rect=(10, 10, 200, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 200, 32))

    def test_generic_toggle_target_id_accepts_checkbox_label_without_type_text(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click this toggle.",
            candidates=[
                ControlCandidate("c001", "Dark mode", "checkbox", (10, 10, 200, 32)),
            ],
            model_rect=(10, 10, 200, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 200, 32))

    def test_generic_switch_target_id_accepts_checkbox_label_without_type_text(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click this switch.",
            candidates=[
                ControlCandidate("c001", "Dark mode", "checkbox", (10, 10, 200, 32)),
            ],
            model_rect=(10, 10, 200, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 200, 32))

    def test_generic_option_target_id_accepts_radio_without_label_match(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Select this option.",
            candidates=[
                ControlCandidate("c001", "Weekly", "radiobutton", (10, 10, 140, 32)),
            ],
            model_rect=(10, 10, 140, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 140, 32))

    def test_generic_option_target_id_accepts_listitem_without_label_match(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Select this option.",
            candidates=[
                ControlCandidate("c001", "Weekly", "listitem", (10, 10, 140, 32)),
            ],
            model_rect=(10, 10, 140, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 140, 32))

    def test_generic_slider_target_id_accepts_slider_without_label_match(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Adjust this slider.",
            candidates=[
                ControlCandidate("c001", "Volume", "slider", (10, 10, 220, 32)),
            ],
            model_rect=(10, 10, 220, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 220, 32))

    def test_generic_spinner_target_id_accepts_spinner_without_label_match(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Adjust this spinner.",
            candidates=[
                ControlCandidate("c001", "History max tokens", "spinner", (10, 10, 160, 32)),
            ],
            model_rect=(10, 10, 160, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 160, 32))

    def test_generic_stepper_target_id_accepts_spinner_without_label_match(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click this stepper.",
            candidates=[
                ControlCandidate("c001", "Retries", "spinner", (10, 10, 120, 32)),
            ],
            model_rect=(10, 10, 120, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 120, 32))

    def test_spinner_stepper_target_id_accepts_adjacent_arrow_buttons(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        candidates = [
            ControlCandidate("spin", "Quantity", "spinner", (100, 100, 120, 32)),
            ControlCandidate("up", "Increase", "button", (224, 100, 24, 16)),
            ControlCandidate("down", "Decrease", "button", (224, 116, 24, 16)),
        ]
        cases = (
            ("Increase the Quantity spinner.", "up", (224, 100, 24, 16)),
            ("Decrease the Quantity spinner.", "down", (224, 116, 24, 16)),
        )
        for instruction, target_id, rect in cases:
            with self.subTest(instruction=instruction):
                result = resolve_candidate_target(
                    target_id=target_id,
                    instruction=instruction,
                    candidates=candidates,
                    model_rect=rect,
                )

                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.source, "target_id")
                self.assertEqual(result.target_id, target_id)
                self.assertFalse(result.rejected_reason)
                self.assertEqual(result.rect, rect)

    def test_text_field_wording_rejects_spinner_target_id(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="spin",
            instruction="Type in this text field.",
            candidates=[
                ControlCandidate("edit", "Name", "edit", (10, 10, 160, 32)),
                ControlCandidate("spin", "Retries", "spinner", (10, 50, 160, 32)),
            ],
            model_rect=(10, 50, 160, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertEqual(result.target_id, "spin")
        self.assertEqual(result.rejected_reason, "target_id control type mismatch")

    def test_generic_hyperlink_target_id_accepts_hyperlink_without_label_match(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click this hyperlink.",
            candidates=[
                ControlCandidate("c001", "Documentation", "hyperlink", (10, 10, 140, 28)),
            ],
            model_rect=(10, 10, 140, 28),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 140, 28))

    def test_generic_list_item_target_id_accepts_listitem_without_label_match(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click this list item.",
            candidates=[
                ControlCandidate("c001", "Settings", "listitem", (10, 10, 160, 32)),
            ],
            model_rect=(10, 10, 160, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 160, 32))

    def test_generic_tree_item_target_id_accepts_treeitem_without_label_match(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click this tree item.",
            candidates=[
                ControlCandidate("c001", "Settings", "treeitem", (10, 10, 160, 32)),
            ],
            model_rect=(10, 10, 160, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 160, 32))

    def test_compact_control_type_target_ids_accept_without_label_match(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        cases = (
            ("listitem", "listitem", (10, 10, 160, 32)),
            ("treeitem", "treeitem", (10, 10, 160, 32)),
            ("menuitem", "menuitem", (10, 10, 160, 28)),
            ("tabitem", "tabitem", (10, 10, 140, 32)),
            ("headeritem", "headeritem", (10, 10, 140, 28)),
            ("splitbutton", "splitbutton", (10, 10, 160, 32)),
        )
        for word, control_type, rect in cases:
            with self.subTest(word=word):
                result = resolve_candidate_target(
                    target_id="c001",
                    instruction=f"Click this {word}.",
                    candidates=[
                        ControlCandidate("c001", "Settings", control_type, rect),
                    ],
                    model_rect=rect,
                )

                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.source, "target_id")
                self.assertFalse(result.rejected_reason)
                self.assertEqual(result.rect, rect)

    def test_generic_split_button_target_id_accepts_splitbutton_without_label_match(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click this split button.",
            candidates=[
                ControlCandidate("c001", "Export", "splitbutton", (10, 10, 160, 32)),
            ],
            model_rect=(10, 10, 160, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 160, 32))

    def test_generic_split_button_target_id_rejects_plain_button(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click this split button.",
            candidates=[
                ControlCandidate("c001", "Export", "button", (10, 10, 160, 32)),
            ],
            model_rect=(10, 10, 160, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertEqual(result.rejected_reason, "target_id control type mismatch")

    def test_browser_address_bar_target_id_accepts_edit(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        cases = (
            "Focus the URL bar.",
            "Click the location bar.",
            "Click the omnibox.",
        )
        for instruction in cases:
            with self.subTest(instruction=instruction):
                result = resolve_candidate_target(
                    target_id="c001",
                    instruction=instruction,
                    candidates=[
                        ControlCandidate("c001", "Address", "edit", (10, 10, 240, 32)),
                    ],
                    model_rect=(10, 10, 240, 32),
                )

                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.source, "target_id")
                self.assertFalse(result.rejected_reason)
                self.assertEqual(result.rect, (10, 10, 240, 32))

    def test_browser_address_bar_target_id_rejects_plain_button(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Focus the URL bar.",
            candidates=[
                ControlCandidate("c001", "Address", "button", (10, 10, 240, 32)),
            ],
            model_rect=(10, 10, 240, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertEqual(result.rejected_reason, "target_id control type mismatch")

    def test_page_address_wording_recovers_from_browser_address_bar_target_id(self) -> None:
        from control_inventory import ControlCandidate
        from agent import _parse_live_help_decision
        from help_session import resolve_help_target

        class Capture:
            width = 1000
            height = 1000
            scale = 1.0
            monitor_left = 0
            monitor_top = 0
            image = None

            def to_screen_coords(self, x: int, y: int) -> tuple[int, int]:
                return x, y

        target = resolve_help_target(
            _parse_live_help_decision(
                json.dumps(
                    {
                        "kind": "step",
                        "instruction": "Click the address field on the page.",
                        "target_id": "browser_address",
                        "target": {"x": 88, "y": 8, "width": 520, "height": 34},
                    }
                )
            ),
            Capture(),
            [
                ControlCandidate(
                    "browser_address",
                    "Address and search bar",
                    "edit",
                    (88, 8, 520, 34),
                    automation_id="address and search bar",
                    window_title="Checkout - Google Chrome",
                ),
                ControlCandidate(
                    "page_address",
                    "Address",
                    "edit",
                    (120, 260, 300, 36),
                    window_title="Checkout - Google Chrome",
                ),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "page_address")
        self.assertEqual(target.rect, (120, 260, 300, 36))
        self.assertFalse(target.rejected_reason)

    def test_browser_address_wording_recovers_from_page_address_target_id(self) -> None:
        from control_inventory import ControlCandidate
        from agent import _parse_live_help_decision
        from help_session import resolve_help_target

        class Capture:
            width = 1000
            height = 1000
            scale = 1.0
            monitor_left = 0
            monitor_top = 0
            image = None

            def to_screen_coords(self, x: int, y: int) -> tuple[int, int]:
                return x, y

        target = resolve_help_target(
            _parse_live_help_decision(
                json.dumps(
                    {
                        "kind": "step",
                        "instruction": "Focus the address bar in Chrome.",
                        "target_id": "page_address",
                        "target": {"x": 120, "y": 260, "width": 300, "height": 36},
                    }
                )
            ),
            Capture(),
            [
                ControlCandidate(
                    "browser_address",
                    "Address and search bar",
                    "edit",
                    (88, 8, 520, 34),
                    automation_id="address and search bar",
                    window_title="Checkout - Google Chrome",
                ),
                ControlCandidate(
                    "page_address",
                    "Address",
                    "edit",
                    (120, 260, 300, 36),
                    window_title="Checkout - Google Chrome",
                ),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "browser_address")
        self.assertEqual(target.rect, (88, 8, 520, 34))
        self.assertFalse(target.rejected_reason)

    def test_taskbar_wording_recovers_from_page_control_target_id(self) -> None:
        from control_inventory import ControlCandidate
        from agent import _parse_live_help_decision
        from help_session import resolve_help_target

        class Capture:
            width = 1200
            height = 1000
            scale = 1.0
            monitor_left = 0
            monitor_top = 0
            image = None

            def to_screen_coords(self, x: int, y: int) -> tuple[int, int]:
                return x, y

        target = resolve_help_target(
            _parse_live_help_decision(
                json.dumps(
                    {
                        "kind": "step",
                        "instruction": "Click taskbar volume.",
                        "target_id": "page_volume",
                        "target": {"x": 120, "y": 260, "width": 140, "height": 36},
                    }
                )
            ),
            Capture(),
            [
                ControlCandidate(
                    "taskbar_volume",
                    "Volume Speakers (Realtek(R) Audio): 24%",
                    "button",
                    (780, 960, 200, 36),
                    automation_id="SystemTrayIcon",
                    window_title="Taskbar",
                ),
                ControlCandidate(
                    "page_volume",
                    "Volume",
                    "button",
                    (120, 260, 140, 36),
                    window_title="Player - Google Chrome",
                ),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "taskbar_volume")
        self.assertEqual(target.rect, (780, 960, 200, 36))
        self.assertFalse(target.rejected_reason)

    def test_page_wording_recovers_from_taskbar_control_target_id(self) -> None:
        from control_inventory import ControlCandidate
        from agent import _parse_live_help_decision
        from help_session import resolve_help_target

        class Capture:
            width = 1200
            height = 1000
            scale = 1.0
            monitor_left = 0
            monitor_top = 0
            image = None

            def to_screen_coords(self, x: int, y: int) -> tuple[int, int]:
                return x, y

        target = resolve_help_target(
            _parse_live_help_decision(
                json.dumps(
                    {
                        "kind": "step",
                        "instruction": "Click volume on the page.",
                        "target_id": "taskbar_volume",
                        "target": {"x": 780, "y": 960, "width": 200, "height": 36},
                    }
                )
            ),
            Capture(),
            [
                ControlCandidate(
                    "taskbar_volume",
                    "Volume Speakers (Realtek(R) Audio): 24%",
                    "button",
                    (780, 960, 200, 36),
                    automation_id="SystemTrayIcon",
                    window_title="Taskbar",
                ),
                ControlCandidate(
                    "page_volume",
                    "Volume",
                    "button",
                    (120, 260, 140, 36),
                    window_title="Player - Google Chrome",
                ),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "page_volume")
        self.assertEqual(target.rect, (120, 260, 140, 36))
        self.assertFalse(target.rejected_reason)

    def test_text_entry_action_target_id_rejects_plain_button(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Type your email.",
            candidates=[
                ControlCandidate("c001", "Email", "button", (10, 10, 180, 32)),
            ],
            model_rect=(10, 10, 180, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertEqual(result.rejected_reason, "target_id control type mismatch")

    def test_text_entry_action_text_match_prefers_edit_over_same_label_button(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        cases = (
            ("Enter the verification code.", "Verification code", (10, 10, 260, 32)),
            ("Fill email address.", "Email", (10, 10, 220, 32)),
        )
        for instruction, label, rect in cases:
            with self.subTest(instruction=instruction):
                result = resolve_candidate_target(
                    target_id="",
                    instruction=instruction,
                    candidates=[
                        ControlCandidate("c001", label, "edit", rect),
                        ControlCandidate("c002", label, "button", (300, 10, 140, 32)),
                    ],
                    model_rect=(300, 10, 140, 32),
                )

                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.source, "text_match")
                self.assertEqual(result.target_id, "c001")
                self.assertEqual(result.rect, rect)
                self.assertFalse(result.rejected_reason)

    def test_text_entry_action_recovers_from_adjacent_label_target(self) -> None:
        from control_inventory import ControlCandidate
        from agent import _parse_live_help_decision
        from help_session import resolve_help_target

        class Capture:
            width = 1000
            height = 1000
            scale = 1.0
            monitor_left = 0
            monitor_top = 0
            image = None

            def to_screen_coords(self, x: int, y: int) -> tuple[int, int]:
                return x, y

        target = resolve_help_target(
            _parse_live_help_decision(
                json.dumps(
                    {
                        "kind": "step",
                        "instruction": "Type into Username.",
                        "target_id": "label",
                        "target": {"x": 80, "y": 100, "width": 80, "height": 24},
                    }
                )
            ),
            Capture(),
            [
                ControlCandidate("label", "Username", "text", (80, 100, 80, 24)),
                ControlCandidate("field", "", "edit", (180, 94, 280, 36)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "field")
        self.assertEqual(target.rect, (180, 94, 280, 36))
        self.assertFalse(target.rejected_reason)

    def test_text_entry_action_recovers_from_wrong_blank_field_when_labeled_field_matches(self) -> None:
        from control_inventory import ControlCandidate
        from agent import _parse_live_help_decision
        from help_session import resolve_help_target

        class Capture:
            width = 1000
            height = 1000
            scale = 1.0
            monitor_left = 0
            monitor_top = 0
            image = None

            def to_screen_coords(self, x: int, y: int) -> tuple[int, int]:
                return x, y

        target = resolve_help_target(
            _parse_live_help_decision(
                json.dumps(
                    {
                        "kind": "step",
                        "instruction": "Type into Password.",
                        "target_id": "wrong",
                        "target": {"x": 100, "y": 94, "width": 260, "height": 36},
                    }
                )
            ),
            Capture(),
            [
                ControlCandidate("wrong", "", "edit", (100, 94, 260, 36)),
                ControlCandidate("label", "Password", "text", (420, 100, 90, 24)),
                ControlCandidate("field", "", "edit", (520, 94, 260, 36)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "field")
        self.assertEqual(target.rect, (520, 94, 260, 36))
        self.assertFalse(target.rejected_reason)

    def test_text_entry_action_recovers_from_password_visibility_button_target(self) -> None:
        from control_inventory import ControlCandidate
        from agent import _parse_live_help_decision
        from help_session import resolve_help_target

        class Capture:
            width = 1000
            height = 1000
            scale = 1.0
            monitor_left = 0
            monitor_top = 0
            image = None

            def to_screen_coords(self, x: int, y: int) -> tuple[int, int]:
                return x, y

        target = resolve_help_target(
            _parse_live_help_decision(
                json.dumps(
                    {
                        "kind": "step",
                        "instruction": "Enter the password.",
                        "target_id": "show",
                        "target": {"x": 430, "y": 98, "width": 28, "height": 28},
                    }
                )
            ),
            Capture(),
            [
                ControlCandidate("label", "Password", "text", (80, 100, 90, 24)),
                ControlCandidate("field", "", "edit", (180, 94, 280, 36)),
                ControlCandidate("show", "Show password", "button", (430, 98, 28, 28)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "field")
        self.assertEqual(target.rect, (180, 94, 280, 36))
        self.assertFalse(target.rejected_reason)

    def test_text_entry_wording_keeps_explicit_enter_button_target_id(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click the Enter button.",
            candidates=[
                ControlCandidate("c001", "Enter", "button", (10, 10, 120, 32)),
            ],
            model_rect=(10, 10, 120, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 120, 32))

    def test_state_and_choice_target_id_rejects_plain_button(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        cases = (
            ("Check Remember me.", "Remember me"),
            ("Turn on dark mode.", "Dark mode"),
            ("Enable notifications.", "Notifications"),
            ("Pick Daily choice.", "Daily"),
            ("Choose Weekly option.", "Weekly"),
            ("Select Yes.", "Yes"),
            ("Open Country select.", "Country"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction):
                result = resolve_candidate_target(
                    target_id="c001",
                    instruction=instruction,
                    candidates=[
                        ControlCandidate("c001", label, "button", (10, 10, 180, 32)),
                    ],
                    model_rect=(10, 10, 180, 32),
                )

                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.source, "target_id")
                self.assertEqual(result.rejected_reason, "target_id control type mismatch")

    def test_state_action_text_match_prefers_checkbox_over_same_label_button(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="",
            instruction="Check Remember me.",
            candidates=[
                ControlCandidate("c001", "Remember me", "checkbox", (10, 10, 180, 32)),
                ControlCandidate("c002", "Remember me", "button", (240, 10, 120, 32)),
            ],
            model_rect=(240, 10, 120, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "text_match")
        self.assertEqual(result.target_id, "c001")
        self.assertEqual(result.rect, (10, 10, 180, 32))
        self.assertFalse(result.rejected_reason)

    def test_state_action_text_match_prefers_matching_action_button_over_noun_checkbox(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="",
            instruction="Enable notifications.",
            candidates=[
                ControlCandidate("c001", "Enable notifications", "button", (10, 10, 190, 32)),
                ControlCandidate("c002", "Notifications", "checkbox", (240, 10, 180, 32)),
            ],
            model_rect=(10, 10, 190, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "text_match")
        self.assertEqual(result.target_id, "c001")
        self.assertEqual(result.rect, (10, 10, 190, 32))
        self.assertFalse(result.rejected_reason)

    def test_explicit_checkbox_wording_recovers_from_state_action_button(self) -> None:
        from control_inventory import ControlCandidate
        from agent import _parse_live_help_decision
        from help_session import resolve_help_target

        class Capture:
            width = 1000
            height = 1000
            scale = 1.0
            monitor_left = 0
            monitor_top = 0
            image = None

            def to_screen_coords(self, x: int, y: int) -> tuple[int, int]:
                return x, y

        for instruction in (
            "Enable notifications checkbox.",
            "Enable notifications toggle.",
            "Enable notifications switch.",
        ):
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    _parse_live_help_decision(
                        json.dumps(
                            {
                                "kind": "step",
                                "instruction": instruction,
                                "target_id": "button",
                                "target": {"x": 320, "y": 100, "width": 190, "height": 32},
                            }
                        )
                    ),
                    Capture(),
                    [
                        ControlCandidate("checkbox", "Notifications", "checkbox", (100, 100, 180, 32)),
                        ControlCandidate("button", "Enable notifications", "button", (320, 100, 190, 32)),
                    ],
                )

                self.assertEqual(target.source, "text_match")
                self.assertEqual(target.target_id, "checkbox")
                self.assertEqual(target.rect, (100, 100, 180, 32))
                self.assertFalse(target.rejected_reason)

    def test_explicit_toggle_in_toolbar_recovers_from_same_label_button(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from agent import _parse_live_help_decision
        from help_session import resolve_help_target

        class Capture:
            width = 1000
            height = 1000
            scale = 1.0
            monitor_left = 0
            monitor_top = 0
            image = None

            def to_screen_coords(self, x: int, y: int) -> tuple[int, int]:
                return x, y

        candidates = [
            ControlCandidate("toggle", "Notifications", "checkbox", (100, 100, 180, 32)),
            ControlCandidate("button", "Notifications", "button", (100, 150, 180, 32)),
        ]
        instruction = "Click the Notifications toggle in the toolbar."

        wrong_target = resolve_candidate_target(
            target_id="button",
            instruction=instruction,
            candidates=candidates,
            model_rect=(100, 150, 180, 32),
        )
        text_target = resolve_candidate_target(
            target_id="",
            instruction=instruction,
            candidates=candidates,
            model_rect=(100, 150, 180, 32),
        )
        snap_target = snap_candidate_target(
            instruction=instruction,
            candidates=candidates,
            model_rect=(100, 150, 180, 32),
        )
        help_target = resolve_help_target(
            _parse_live_help_decision(
                json.dumps(
                    {
                        "kind": "step",
                        "instruction": instruction,
                        "target_id": "button",
                        "target": {"x": 100, "y": 150, "width": 180, "height": 32},
                    }
                )
            ),
            Capture(),
            candidates,
        )

        self.assertEqual(wrong_target.target_id, "button")
        self.assertEqual(wrong_target.rejected_reason, "target_id control type mismatch")
        for resolved in (text_target, snap_target, help_target):
            self.assertEqual(resolved.target_id, "toggle")
            self.assertFalse(resolved.rejected_reason)
            self.assertEqual(resolved.rect, (100, 100, 180, 32))

    def test_state_action_target_id_accepts_matching_action_button(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Enable notifications.",
            candidates=[
                ControlCandidate("c001", "Enable notifications", "button", (10, 10, 190, 32)),
                ControlCandidate("c002", "Notifications", "checkbox", (240, 10, 180, 32)),
            ],
            model_rect=(10, 10, 190, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertEqual(result.target_id, "c001")
        self.assertFalse(result.rejected_reason)

    def test_choice_wording_text_match_prefers_radio_over_same_label_button(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        cases = (
            (
                "Pick Daily choice.",
                ControlCandidate("c001", "Daily", "radiobutton", (10, 10, 180, 32)),
                ControlCandidate("c002", "Daily", "button", (240, 10, 120, 32)),
            ),
            (
                "Select Yes.",
                ControlCandidate("c001", "Yes", "radiobutton", (10, 10, 80, 32)),
                ControlCandidate("c002", "Yes", "button", (240, 10, 80, 32)),
            ),
            (
                "Open Country select.",
                ControlCandidate("c001", "Country", "combobox", (10, 10, 180, 32)),
                ControlCandidate("c002", "Country", "button", (240, 10, 120, 32)),
            ),
        )
        for instruction, expected, decoy in cases:
            with self.subTest(instruction=instruction):
                result = resolve_candidate_target(
                    target_id="",
                    instruction=instruction,
                    candidates=[expected, decoy],
                    model_rect=decoy.rect,
                )

                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.source, "text_match")
                self.assertEqual(result.target_id, "c001")
                self.assertEqual(result.rect, expected.rect)
                self.assertFalse(result.rejected_reason)

    def test_check_for_updates_keeps_button_target_id(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Check for updates.",
            candidates=[
                ControlCandidate("c001", "Check for updates", "button", (10, 10, 180, 32)),
            ],
            model_rect=(10, 10, 180, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 180, 32))

    def test_button_control_suffix_target_id_accepts_button(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click this button control.",
            candidates=[
                ControlCandidate("c001", "Submit", "button", (10, 10, 120, 32)),
            ],
            model_rect=(10, 10, 120, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 120, 32))

    def test_literal_edit_target_id_accepts_edit(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click this edit control.",
            candidates=[
                ControlCandidate("c001", "Search", "edit", (10, 10, 240, 32)),
            ],
            model_rect=(10, 10, 240, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 240, 32))

    def test_contextual_container_target_id_accepts_exact_control(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        cases = (
            ("Click this toolbar button.", "Save", "button", (10, 10, 100, 32)),
            ("Click Toolbar button.", "Toolbar", "button", (10, 10, 100, 32)),
            ("Click this toolbar icon.", "Settings", "button", (10, 10, 32, 32)),
            ("Click this form field.", "Name", "edit", (10, 10, 240, 32)),
            ("Click this dialog button.", "OK", "button", (10, 10, 80, 32)),
            ("Click this modal button.", "OK", "button", (10, 10, 80, 32)),
            ("Click this panel button.", "Save", "button", (10, 10, 100, 32)),
            ("Click Panel button.", "Panel", "button", (10, 10, 100, 32)),
            ("Click this table row.", "Order 123", "listitem", (10, 10, 240, 32)),
            ("Click this grid row.", "Order 123", "listitem", (10, 10, 240, 32)),
            ("Click this page link.", "Docs", "hyperlink", (10, 10, 120, 28)),
            ("Click this card checkbox.", "Done", "checkbox", (10, 10, 160, 32)),
            ("Click this section toggle.", "Dark mode", "checkbox", (10, 10, 160, 32)),
            ("Click this drawer item.", "Settings", "listitem", (10, 10, 160, 32)),
            ("Click this pane button.", "Apply", "button", (10, 10, 100, 32)),
            ("Click this popup menu item.", "Open", "menuitem", (10, 10, 120, 28)),
            ("Click this navigation tab.", "Settings", "tabitem", (10, 10, 140, 32)),
            ("Click this sidebar item.", "Settings", "listitem", (10, 10, 160, 32)),
            ("Click this nav item.", "Settings", "listitem", (10, 10, 160, 32)),
        )
        for instruction, label, control_type, rect in cases:
            with self.subTest(instruction=instruction):
                result = resolve_candidate_target(
                    target_id="c001",
                    instruction=instruction,
                    candidates=[
                        ControlCandidate("c001", label, control_type, rect),
                    ],
                    model_rect=rect,
                )

                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.source, "target_id")
                self.assertFalse(result.rejected_reason)
                self.assertEqual(result.rect, rect)

    def test_switch_account_text_match_still_allows_button(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="",
            instruction="Click Switch account.",
            candidates=[
                ControlCandidate("c001", "Switch account", "button", (10, 10, 140, 32)),
                ControlCandidate("c002", "Dark mode", "checkbox", (10, 60, 140, 32)),
            ],
            model_rect=(10, 10, 140, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "text_match")
        self.assertEqual(result.target_id, "c001")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 140, 32))

    def test_toggle_sidebar_text_match_still_allows_button(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="",
            instruction="Click Toggle sidebar.",
            candidates=[
                ControlCandidate("c001", "Toggle sidebar", "button", (10, 10, 150, 32)),
                ControlCandidate("c002", "Dark mode", "checkbox", (10, 60, 140, 32)),
            ],
            model_rect=(10, 10, 150, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "text_match")
        self.assertEqual(result.target_id, "c001")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 150, 32))

    def test_generic_column_header_target_id_accepts_header_without_label_match(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click this column header.",
            candidates=[
                ControlCandidate("c001", "Status", "headeritem", (100, 50, 120, 28)),
            ],
            model_rect=(100, 50, 120, 28),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (100, 50, 120, 28))

    def test_generic_checkbox_target_id_rejects_wrong_button_type(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click this checkbox.",
            candidates=[
                ControlCandidate("c001", "", "button", (10, 10, 32, 32)),
            ],
            model_rect=(10, 10, 32, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertEqual(result.rejected_reason, "target_id control type mismatch")

    def test_target_id_foreground_duplicate_without_geometry_is_accepted(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c002",
            instruction="Click Save.",
            candidates=[
                ControlCandidate("c001", "Save", "button", (10, 10, 60, 30), window_rank=2),
                ControlCandidate("c002", "Save", "button", (300, 10, 60, 30), window_rank=0),
            ],
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.source, "target_id")
        self.assertEqual(result.target_id, "c002")

    def test_target_id_background_duplicate_without_geometry_is_rejected(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click Save.",
            candidates=[
                ControlCandidate("c001", "Save", "button", (10, 10, 60, 30), window_rank=2),
                ControlCandidate("c002", "Save", "button", (300, 10, 60, 30), window_rank=0),
            ],
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertEqual(result.rejected_reason, "target_id ambiguous")

    def test_target_id_background_duplicate_with_geometry_is_rejected(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click Save.",
            candidates=[
                ControlCandidate("c001", "Save", "button", (10, 10, 60, 30), window_rank=2),
                ControlCandidate("c002", "Save", "button", (300, 10, 60, 30), window_rank=0),
            ],
            model_rect=(10, 10, 60, 30),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertEqual(result.rejected_reason, "target_id ambiguous")

    def test_text_match_prefers_foreground_duplicate_across_windows(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="",
            instruction="Click Save.",
            candidates=[
                ControlCandidate("c001", "Save", "button", (10, 10, 60, 30), window_rank=2),
                ControlCandidate("c002", "Save", "button", (300, 10, 60, 30), window_rank=0),
            ],
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.source, "text_match")
        self.assertEqual(result.target_id, "c002")

    def test_unlabeled_target_id_with_nearby_unlabeled_competitor_is_rejected(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c002",
            instruction="Click this button.",
            candidates=[
                ControlCandidate("c001", "", "button", (100, 10, 32, 32)),
                ControlCandidate("c002", "", "button", (140, 10, 32, 32)),
            ],
            model_rect=(140, 10, 32, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.rejected_reason, "target_id ambiguous unlabeled control")

    def test_icon_only_target_id_with_nearby_icon_ignores_automation_ids_for_ambiguity(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c002",
            instruction="Click this icon.",
            candidates=[
                ControlCandidate("c001", "", "button", (100, 10, 32, 32), automation_id="helperPrecisionIconA"),
                ControlCandidate("c002", "", "button", (140, 10, 32, 32), automation_id="helperPrecisionIconB"),
            ],
            model_rect=(140, 10, 32, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.rejected_reason, "target_id ambiguous unlabeled control")

    def test_resolve_text_match_beats_nearby_wrong_button(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        candidates = [
            ControlCandidate("c001", "Cancel", "button", (10, 10, 60, 30)),
            ControlCandidate("c002", "Submit", "button", (100, 10, 60, 30)),
        ]

        result = resolve_candidate_target(
            target_id="",
            instruction="Click the Submit button.",
            candidates=candidates,
            model_rect=(10, 10, 60, 30),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "text_match")
        self.assertEqual(result.target_id, "c002")

    def test_resolve_low_confidence_returns_none(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        candidates = [ControlCandidate("c001", "Cancel", "button", (10, 10, 60, 30))]

        result = resolve_candidate_target(
            target_id="",
            instruction="Click Continue.",
            candidates=candidates,
        )

        self.assertIsNone(result)

    def test_resolve_unknown_target_id_is_rejected(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c999",
            instruction="Click Continue.",
            candidates=[ControlCandidate("c001", "Continue", "button", (10, 10, 60, 30))],
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.rejected_reason, "unknown target_id")

    def test_ambiguous_text_match_returns_rejected_resolution(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="",
            instruction="Click Save.",
            candidates=[
                ControlCandidate("c001", "Save", "button", (10, 10, 60, 30)),
                ControlCandidate("c002", "Save", "button", (100, 10, 60, 30)),
            ],
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.rejected_reason, "ambiguous text match")

    def test_text_match_ignores_same_visual_duplicate(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="",
            instruction="Click Save.",
            candidates=[
                ControlCandidate("c001", "Save", "button", (10, 10, 60, 30), automation_id="save-a"),
                ControlCandidate("c002", "Save", "button", (10, 10, 60, 30), automation_id="save-b"),
            ],
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.target_id, "c001")

    def test_norm_rect_clips_partially_offscreen_candidate(self) -> None:
        from control_inventory import collect_control_candidates, format_candidates_for_prompt

        button = _make_button("Edge", -20, 120, 60, 30)
        window = _make_window("App", -40, 0, 200, 600, [button])
        desktop = _FakeDesktop([window])
        candidates = collect_control_candidates(
            self._capture(),
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        prompt = format_candidates_for_prompt(candidates, self._capture())

        self.assertIn("Edge", prompt)
        self.assertIn("norm=(0,200,50,50)", prompt)

    def test_collect_prefers_tighter_child_over_matching_container(self) -> None:
        from control_inventory import collect_control_candidates

        child = _make_button("Save", 120, 120, 50, 24)
        parent = _FakeControl(
            text="Save",
            control_type="Button",
            rect=_FakeRect(100, 100, 240, 180),
            children=[child],
        )
        window = _make_window("App", 0, 0, 800, 600, [parent])
        desktop = _FakeDesktop([window])

        candidates = collect_control_candidates(
            self._capture(),
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].rect, (120, 120, 50, 24))

    def test_labeled_parent_is_not_pruned_by_unlabeled_child_glyph(self) -> None:
        from control_inventory import collect_control_candidates

        glyph = _FakeControl(
            text="",
            control_type="Button",
            rect=_FakeRect(104, 104, 124, 124),
        )
        parent = _FakeControl(
            text="Enable sync",
            control_type="CheckBox",
            rect=_FakeRect(100, 100, 220, 132),
            children=[glyph],
        )
        window = _make_window("Settings", 0, 0, 800, 600, [parent])
        desktop = _FakeDesktop([window])

        candidates = collect_control_candidates(
            self._capture(),
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertTrue(any(candidate.text == "Enable sync" for candidate in candidates))

    def test_visible_parent_is_not_pruned_by_automation_only_child_glyph(self) -> None:
        from control_inventory import collect_control_candidates

        glyph = _FakeControl(
            text="",
            control_type="Button",
            rect=_FakeRect(104, 104, 124, 124),
            automation_id="saveChangesIcon",
        )
        parent = _FakeControl(
            text="Save changes",
            control_type="Button",
            rect=_FakeRect(100, 100, 240, 132),
            children=[glyph],
        )
        window = _make_window("Editor", 0, 0, 800, 600, [parent])
        desktop = _FakeDesktop([window])

        candidates = collect_control_candidates(
            self._capture(),
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertGreaterEqual(len(candidates), 2)
        self.assertEqual(candidates[0].text, "Save changes")
        self.assertEqual(candidates[0].rect, (100, 100, 140, 32))
        self.assertTrue(any(candidate.automation_id == "saveChangesIcon" for candidate in candidates))

    def test_collect_prioritizes_foreground_window_before_screen_position(self) -> None:
        from control_inventory import collect_control_candidates

        background_buttons = [
            _make_button(f"Browser {index}", 10 + index * 12, 10, 10, 24)
            for index in range(12)
        ]
        background = _make_window("Browser", 0, 0, 800, 120, background_buttons, handle=101)
        save = _make_button("Save changes", 120, 500, 90, 32)
        foreground = _make_window("Editor", 0, 420, 800, 180, [save], handle=202)
        desktop = _FakeDesktop([background, foreground])

        candidates = collect_control_candidates(
            self._capture(),
            desktop_factory=lambda: desktop,
            foreground_handle_provider=lambda: 202,
            timeout_ms=2000,
            limit=3,
        )

        self.assertTrue(candidates)
        self.assertEqual(candidates[0].text, "Save changes")
        self.assertEqual(candidates[0].id, "c001")

    def test_collect_visits_foreground_window_before_background_windows(self) -> None:
        from control_inventory import collect_control_candidates

        visits: list[str] = []
        background = _RecordingControl(
            text="Background",
            control_type="Window",
            rect=_FakeRect(0, 0, 800, 300),
            handle=101,
            children=[_make_button("Background button", 20, 20, 120, 30)],
            visits=visits,
        )
        foreground = _RecordingControl(
            text="Foreground",
            control_type="Window",
            rect=_FakeRect(0, 320, 800, 620),
            handle=202,
            children=[_make_button("Foreground button", 20, 340, 120, 30)],
            visits=visits,
        )
        desktop = _FakeDesktop([background, foreground])

        collect_control_candidates(
            self._capture(),
            desktop_factory=lambda: desktop,
            foreground_handle_provider=lambda: 202,
            timeout_ms=2000,
        )

        self.assertGreaterEqual(len(visits), 2)
        self.assertEqual(visits[:2], ["Foreground", "Background"])

    def test_collect_skips_own_process_top_level_windows(self) -> None:
        from control_inventory import collect_control_candidates

        helper_button = _make_button("Helper settings", 20, 20, 120, 30)
        helper_window = _make_window("Helper", 0, 0, 200, 120, [helper_button], handle=101)
        app_button = _make_button("Save changes", 220, 20, 120, 30)
        app_window = _make_window("Editor", 200, 0, 300, 120, [app_button], handle=202)
        desktop = _FakeDesktop([helper_window, app_window])

        with patch("control_inventory._is_own_process_window", side_effect=lambda hwnd: hwnd == 101):
            candidates = collect_control_candidates(
                self._capture(),
                desktop_factory=lambda: desktop,
                timeout_ms=2000,
            )

        self.assertEqual([candidate.text for candidate in candidates], ["Save changes"])
        self.assertEqual(candidates[0].window_title, "Editor")

    def test_collect_skips_occluded_background_window_candidates(self) -> None:
        from control_inventory import collect_control_candidates

        save = _make_button("Save changes", 40, 40, 120, 30)
        background = _make_window("Background Editor", 0, 0, 240, 140, [save], handle=101)
        dismiss = _make_button("Dismiss", 50, 45, 100, 30)
        foreground = _make_window("Blocking Dialog", 20, 20, 220, 120, [dismiss], handle=202)
        desktop = _FakeDesktop([background, foreground])

        def topmost_at(x: int, y: int) -> int:
            if 20 <= x < 240 and 20 <= y < 140:
                return 202
            return 101

        candidates = collect_control_candidates(
            self._capture(),
            desktop_factory=lambda: desktop,
            topmost_handle_provider=topmost_at,
            timeout_ms=2000,
        )

        labels = [candidate.text for candidate in candidates]
        self.assertIn("Dismiss", labels)
        self.assertNotIn("Save changes", labels)

    def test_collect_skips_candidate_when_click_center_is_occluded(self) -> None:
        from control_inventory import collect_control_candidates

        save = _make_button("Save changes", 40, 40, 120, 30)
        background = _make_window("Background Editor", 0, 0, 240, 140, [save], handle=101)
        dismiss = _make_button("Dismiss", 90, 45, 80, 30)
        foreground = _make_window("Blocking Dialog", 80, 35, 100, 50, [dismiss], handle=202)
        desktop = _FakeDesktop([background, foreground])

        def topmost_at(x: int, y: int) -> int:
            if 80 <= x < 180 and 35 <= y < 85:
                return 202
            return 101

        candidates = collect_control_candidates(
            self._capture(),
            desktop_factory=lambda: desktop,
            topmost_handle_provider=topmost_at,
            timeout_ms=2000,
        )

        labels = [candidate.text for candidate in candidates]
        self.assertIn("Dismiss", labels)
        self.assertNotIn("Save changes", labels)

    def test_snap_candidate_target_reuses_collected_candidate_snapshot(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click Save.",
            candidates=[ControlCandidate("c001", "Save", "button", (100, 100, 50, 24))],
            model_rect=(96, 96, 60, 30),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.rect, (100, 100, 50, 24))

    def test_snap_candidate_target_accepts_deictic_exact_labeled_control(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click here.",
            candidates=[ControlCandidate("c001", "Save", "button", (100, 100, 80, 32))],
            model_rect=(100, 100, 80, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c001")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (100, 100, 80, 32))

    def test_snap_candidate_target_prefers_tight_action_inside_matching_row(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click Settings.",
            candidates=[
                ControlCandidate("c001", "Settings", "listitem", (10, 10, 600, 80)),
                ControlCandidate("c002", "Settings", "button", (20, 20, 70, 30)),
            ],
            model_rect=(10, 10, 600, 80),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c002")
        self.assertEqual(result.rect, (20, 20, 70, 30))

    def test_snap_candidate_target_rejects_generic_row_containing_tight_actions(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click this button.",
            candidates=[
                ControlCandidate("c001", "Account row", "listitem", (10, 10, 600, 80)),
                ControlCandidate("c002", "Edit", "button", (450, 20, 60, 30)),
                ControlCandidate("c003", "Delete", "button", (520, 20, 70, 30)),
            ],
            model_rect=(10, 10, 600, 80),
        )

        self.assertIsNone(result)

    def test_snap_candidate_target_rejects_action_mismatch_with_generic_overlap(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        cases = (
            ("Click Send message.", "Delete message"),
            ("Delete message.", "Send message"),
            ("Save document.", "Delete document"),
            ("Download file.", "Upload file"),
            ("Remove member.", "Add member"),
            ("Add member.", "Remove member"),
            ("Approve request.", "Reject request"),
            ("Reject request.", "Approve request"),
            ("Accept invite.", "Decline invite"),
            ("Decline invite.", "Accept invite"),
            ("Allow access.", "Deny access"),
            ("Deny access.", "Allow access"),
            ("Sort by name.", "Filter by name"),
            ("Filter by name.", "Sort by name"),
            ("Refresh feed.", "Reload page"),
            ("Reload page.", "Refresh feed"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                result = snap_candidate_target(
                    instruction=instruction,
                    candidates=[
                        ControlCandidate("c001", label, "button", (120, 160, 180, 32)),
                    ],
                    model_rect=(120, 160, 180, 32),
                )

                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.source, "candidate_snap")
                self.assertEqual(result.target_id, "c001")
                self.assertEqual(result.rejected_reason, "candidate semantic mismatch")

    def test_snap_candidate_target_accepts_matching_action_with_generic_overlap(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        cases = (
            ("Click Send message.", "Submit message"),
            ("Save document.", "Floppy disk"),
            ("Upload file.", "Choose file"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                result = snap_candidate_target(
                    instruction=instruction,
                    candidates=[
                        ControlCandidate("c001", label, "button", (120, 160, 180, 32)),
                    ],
                    model_rect=(120, 160, 180, 32),
                )

                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.source, "candidate_snap")
                self.assertEqual(result.target_id, "c001")
                self.assertFalse(result.rejected_reason)

    def test_snap_candidate_target_rejects_opposite_history_action(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        cases = (
            ("Undo change.", "Redo change"),
            ("Redo change.", "Undo change"),
            ("Undo last change.", "Redo last change"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                result = snap_candidate_target(
                    instruction=instruction,
                    candidates=[
                        ControlCandidate("c001", label, "button", (120, 160, 160, 32)),
                    ],
                    model_rect=(120, 160, 160, 32),
                )

                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.source, "candidate_snap")
                self.assertEqual(result.target_id, "c001")
                self.assertEqual(result.rejected_reason, "candidate semantic mismatch")

    def test_snap_candidate_target_rejects_opposite_checkbox_state_action(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        cases = (
            ("Enable notifications.", "Disable notifications"),
            ("Disable notifications.", "Enable notifications"),
            ("Check Remember me.", "Uncheck Remember me"),
            ("Uncheck Remember me.", "Check Remember me"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                result = snap_candidate_target(
                    instruction=instruction,
                    candidates=[
                        ControlCandidate("c001", label, "checkbox", (120, 160, 180, 32)),
                    ],
                    model_rect=(120, 160, 180, 32),
                )

                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.source, "candidate_snap")
                self.assertEqual(result.target_id, "c001")
                self.assertEqual(result.rejected_reason, "candidate semantic mismatch")

    def test_snap_candidate_target_accepts_matching_state_action_button(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Enable notifications.",
            candidates=[
                ControlCandidate("c001", "Enable notifications", "button", (120, 160, 190, 32)),
                ControlCandidate("c002", "Notifications", "checkbox", (360, 160, 180, 32)),
            ],
            model_rect=(120, 160, 190, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c001")
        self.assertFalse(result.rejected_reason)

    def test_snap_candidate_target_rejects_navigation_media_transport_collision(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        cases = (
            ("Go forward.", "Next track"),
            ("Go forward.", "Next song"),
            ("Go back.", "Previous track"),
            ("Go back.", "Previous song"),
            ("Go back.", "Back up and sync"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                result = snap_candidate_target(
                    instruction=instruction,
                    candidates=[
                        ControlCandidate("c001", label, "button", (120, 160, 140, 32)),
                    ],
                    model_rect=(120, 160, 140, 32),
                )

                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.source, "candidate_snap")
                self.assertEqual(result.target_id, "c001")
                self.assertEqual(result.rejected_reason, "candidate semantic mismatch")

    def test_snap_candidate_target_prefers_splitbutton_menu_segment(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Open the Export menu.",
            candidates=[
                ControlCandidate("c001", "Export", "splitbutton", (100, 100, 180, 32)),
                ControlCandidate("c002", "Export", "button", (100, 100, 140, 32)),
                ControlCandidate("c003", "Export menu", "menuitem", (240, 100, 40, 32)),
            ],
            model_rect=(100, 100, 180, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c003")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (240, 100, 40, 32))

    def test_snap_candidate_target_prefers_splitbutton_dropdown_segment(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Open the Export dropdown.",
            candidates=[
                ControlCandidate("c001", "Export", "splitbutton", (100, 100, 180, 32)),
                ControlCandidate("c002", "Export", "button", (100, 100, 140, 32)),
                ControlCandidate("c003", "Export menu", "menuitem", (240, 100, 40, 32)),
            ],
            model_rect=(100, 100, 180, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c003")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (240, 100, 40, 32))

    def test_snap_candidate_target_rejects_splitbutton_menu_without_precise_segment(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Open the Export menu.",
            candidates=[
                ControlCandidate("c001", "Export", "splitbutton", (100, 100, 180, 32)),
            ],
            model_rect=(100, 100, 180, 32),
        )

        self.assertIsNone(result)

    def test_snap_candidate_target_accepts_menu_launcher_button(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        cases = (
            "Open the overflow menu.",
            "Click the kebab menu.",
            "Click the three dots menu.",
            "Open the More options menu.",
        )
        for instruction in cases:
            with self.subTest(instruction=instruction):
                result = snap_candidate_target(
                    instruction=instruction,
                    candidates=[
                        ControlCandidate("c001", "More options", "button", (10, 10, 120, 32)),
                    ],
                    model_rect=(10, 10, 120, 32),
                )

                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.source, "candidate_snap")
                self.assertEqual(result.target_id, "c001")
                self.assertFalse(result.rejected_reason)
                self.assertEqual(result.rect, (10, 10, 120, 32))

    def test_snap_candidate_target_accepts_common_button_aliases(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        cases = (
            ("Click Confirm.", "OK", "ok"),
            ("Click Previous.", "Back", "back"),
            ("Click Continue.", "Next", "next"),
            ("Click Sign in.", "Log in", "login"),
        )
        for instruction, label, candidate_id in cases:
            with self.subTest(instruction=instruction):
                result = snap_candidate_target(
                    instruction=instruction,
                    candidates=[
                        ControlCandidate(candidate_id, label, "button", (10, 10, 120, 32)),
                    ],
                    model_rect=(10, 10, 120, 32),
                )

                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.source, "candidate_snap")
                self.assertEqual(result.target_id, candidate_id)
                self.assertFalse(result.rejected_reason)

    def test_snap_candidate_target_prefers_disclosure_button_inside_broad_row(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        cases = (
            ("Click the chevron.", "Expand", "c002"),
            ("Click the down arrow.", "Expand", "c002"),
            ("Expand Advanced settings.", "Expand", "c002"),
            ("Collapse Advanced settings.", "Collapse", "c003"),
        )
        for instruction, label, candidate_id in cases:
            with self.subTest(instruction=instruction):
                result = snap_candidate_target(
                    instruction=instruction,
                    candidates=[
                        ControlCandidate("c001", "Advanced settings", "listitem", (10, 10, 500, 80)),
                        ControlCandidate(candidate_id, label, "button", (468, 36, 28, 28)),
                    ],
                    model_rect=(10, 10, 500, 80),
                )

                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.source, "candidate_snap")
                self.assertEqual(result.target_id, candidate_id)
                self.assertFalse(result.rejected_reason)
                self.assertEqual(result.rect, (468, 36, 28, 28))

    def test_snap_candidate_target_rejects_selector_wording_on_plain_button(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Open the Country selector.",
            candidates=[
                ControlCandidate("c001", "Country", "button", (10, 10, 120, 32)),
            ],
            model_rect=(10, 10, 120, 32),
        )

        self.assertIsNone(result)

    def test_snap_candidate_target_accepts_selector_combobox_in_broad_rect(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Open the Country selector.",
            candidates=[
                ControlCandidate("c001", "Country", "combobox", (10, 10, 220, 32)),
                ControlCandidate("c002", "Country", "button", (280, 10, 100, 32)),
            ],
            model_rect=(10, 10, 370, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c001")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 220, 32))

    def test_snap_candidate_target_keeps_explicit_picker_button(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click the picker button.",
            candidates=[
                ControlCandidate("c001", "Picker", "button", (10, 10, 120, 32)),
            ],
            model_rect=(10, 10, 120, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c001")
        self.assertFalse(result.rejected_reason)

    def test_snap_candidate_target_accepts_contextual_picker_launcher_button(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        cases = (
            ("Open the Date picker.", "Date"),
            ("Click the Calendar picker.", "Calendar"),
            ("Open the Color picker.", "Color"),
            ("Click the File picker.", "Choose file"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction):
                result = snap_candidate_target(
                    instruction=instruction,
                    candidates=[
                        ControlCandidate("c001", label, "button", (10, 10, 140, 32)),
                    ],
                    model_rect=(10, 10, 140, 32),
                )

                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.source, "candidate_snap")
                self.assertEqual(result.target_id, "c001")
                self.assertFalse(result.rejected_reason)

    def test_snap_candidate_target_accepts_file_action_alias_button(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        cases = (
            ("Upload a file.", "Browse"),
            ("Open the file picker.", "Browse"),
            ("Choose a file.", "Browse"),
            ("Attach a document.", "Choose file"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction):
                result = snap_candidate_target(
                    instruction=instruction,
                    candidates=[
                        ControlCandidate("c001", label, "button", (10, 10, 140, 32)),
                    ],
                    model_rect=(10, 10, 140, 32),
                )

                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.source, "candidate_snap")
                self.assertEqual(result.target_id, "c001")
                self.assertFalse(result.rejected_reason)

    def test_snap_candidate_target_accepts_generic_field_containing_clear_action(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click this field.",
            candidates=[
                ControlCandidate("c001", "Search", "edit", (10, 10, 600, 40)),
                ControlCandidate("c002", "Clear", "button", (570, 14, 28, 28)),
            ],
            model_rect=(10, 10, 600, 40),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c001")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 600, 40))

    def test_snap_candidate_target_accepts_generic_text_box_without_placeholder_match(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click this text box.",
            candidates=[
                ControlCandidate("c001", "Search", "edit", (10, 10, 600, 40)),
                ControlCandidate("c002", "Clear", "button", (570, 14, 28, 28)),
            ],
            model_rect=(10, 10, 600, 40),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c001")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 600, 40))

    def test_snap_candidate_target_accepts_generic_checkbox_label_without_type_text(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click this checkbox.",
            candidates=[
                ControlCandidate("c001", "Enable precision mode", "checkbox", (10, 10, 200, 32)),
            ],
            model_rect=(10, 10, 200, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c001")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 200, 32))

    def test_snap_candidate_target_rejects_contained_checkbox_with_only_row_context(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click the Archive checkbox in Billing row.",
            candidates=[
                ControlCandidate("c001", "Billing row", "listitem", (10, 10, 600, 80)),
                ControlCandidate("c002", "Done", "checkbox", (24, 34, 20, 20)),
            ],
            model_rect=(10, 10, 600, 80),
        )

        self.assertIsNone(result)

    def test_snap_candidate_target_accepts_generic_toggle_checkbox(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click this toggle.",
            candidates=[
                ControlCandidate("c001", "Dark mode", "checkbox", (10, 10, 200, 32)),
            ],
            model_rect=(10, 10, 200, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c001")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 200, 32))

    def test_snap_candidate_target_accepts_generic_switch_checkbox(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click this switch.",
            candidates=[
                ControlCandidate("c001", "Dark mode", "checkbox", (10, 10, 200, 32)),
            ],
            model_rect=(10, 10, 200, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c001")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 200, 32))

    def test_snap_candidate_target_accepts_generic_radio_option(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Select this option.",
            candidates=[
                ControlCandidate("c001", "Weekly", "radiobutton", (10, 10, 140, 32)),
            ],
            model_rect=(10, 10, 140, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c001")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 140, 32))

    def test_snap_candidate_target_rejects_broad_radio_option_group(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Select this option.",
            candidates=[
                ControlCandidate("c001", "Daily", "radiobutton", (10, 10, 140, 32)),
                ControlCandidate("c002", "Weekly", "radiobutton", (10, 42, 140, 32)),
                ControlCandidate("c003", "Monthly", "radiobutton", (10, 74, 140, 32)),
            ],
            model_rect=(10, 10, 140, 96),
        )

        self.assertIsNone(result)

    def test_snap_candidate_target_accepts_generic_slider(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Adjust this slider.",
            candidates=[
                ControlCandidate("c001", "Volume", "slider", (10, 10, 220, 32)),
            ],
            model_rect=(10, 10, 220, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c001")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 220, 32))

    def test_snap_candidate_target_rejects_broad_slider_group(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Adjust this slider.",
            candidates=[
                ControlCandidate("c001", "Volume", "slider", (10, 10, 220, 32)),
                ControlCandidate("c002", "Brightness", "slider", (10, 50, 220, 32)),
                ControlCandidate("c003", "Contrast", "slider", (10, 90, 220, 32)),
            ],
            model_rect=(10, 10, 220, 112),
        )

        self.assertIsNone(result)

    def test_snap_candidate_target_accepts_generic_spinner(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Adjust this spinner.",
            candidates=[
                ControlCandidate("c001", "History max tokens", "spinner", (10, 10, 160, 32)),
            ],
            model_rect=(10, 10, 160, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c001")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 160, 32))

    def test_snap_candidate_target_accepts_adjacent_spinner_stepper_button(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        candidates = [
            ControlCandidate("spin", "Quantity", "spinner", (100, 100, 120, 32)),
            ControlCandidate("up", "Increase", "button", (224, 100, 24, 16)),
            ControlCandidate("down", "Decrease", "button", (224, 116, 24, 16)),
        ]
        cases = (
            ("Increase the Quantity spinner.", "up", (224, 100, 24, 16)),
            ("Decrease the Quantity spinner.", "down", (224, 116, 24, 16)),
        )
        for instruction, target_id, rect in cases:
            with self.subTest(instruction=instruction):
                result = snap_candidate_target(
                    instruction=instruction,
                    candidates=candidates,
                    model_rect=rect,
                )

                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.source, "candidate_snap")
                self.assertEqual(result.target_id, target_id)
                self.assertFalse(result.rejected_reason)
                self.assertEqual(result.rect, rect)

    def test_snap_candidate_target_rejects_broad_spinner_group(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Adjust this spinner.",
            candidates=[
                ControlCandidate("c001", "Temperature", "spinner", (10, 10, 120, 32)),
                ControlCandidate("c002", "Retries", "spinner", (10, 50, 120, 32)),
                ControlCandidate("c003", "Delay", "spinner", (10, 90, 120, 32)),
            ],
            model_rect=(10, 10, 120, 112),
        )

        self.assertIsNone(result)

    def test_snap_candidate_target_accepts_generic_hyperlink(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click this hyperlink.",
            candidates=[
                ControlCandidate("c001", "Documentation", "hyperlink", (10, 10, 140, 28)),
            ],
            model_rect=(10, 10, 140, 28),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c001")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 140, 28))

    def test_snap_candidate_target_rejects_broad_hyperlink_group(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click this hyperlink.",
            candidates=[
                ControlCandidate("c001", "Docs", "hyperlink", (10, 10, 120, 28)),
                ControlCandidate("c002", "Support", "hyperlink", (10, 46, 120, 28)),
                ControlCandidate("c003", "Pricing", "hyperlink", (10, 82, 120, 28)),
            ],
            model_rect=(10, 10, 120, 100),
        )

        self.assertIsNone(result)

    def test_snap_candidate_target_accepts_generic_list_item(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click this list item.",
            candidates=[
                ControlCandidate("c001", "Settings", "listitem", (10, 10, 160, 32)),
            ],
            model_rect=(10, 10, 160, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c001")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 160, 32))

    def test_snap_candidate_target_rejects_broad_list_item_group(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click this list item.",
            candidates=[
                ControlCandidate("c001", "General", "listitem", (10, 10, 160, 32)),
                ControlCandidate("c002", "Privacy", "listitem", (10, 50, 160, 32)),
                ControlCandidate("c003", "Billing", "listitem", (10, 90, 160, 32)),
            ],
            model_rect=(10, 10, 160, 112),
        )

        self.assertIsNone(result)

    def test_snap_candidate_target_accepts_generic_tree_item(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click this tree item.",
            candidates=[
                ControlCandidate("c001", "Settings", "treeitem", (10, 10, 160, 32)),
            ],
            model_rect=(10, 10, 160, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c001")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 160, 32))

    def test_snap_candidate_target_rejects_broad_tree_item_group(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click this tree item.",
            candidates=[
                ControlCandidate("c001", "src", "treeitem", (10, 10, 160, 32)),
                ControlCandidate("c002", "tests", "treeitem", (10, 50, 160, 32)),
                ControlCandidate("c003", "docs", "treeitem", (10, 90, 160, 32)),
            ],
            model_rect=(10, 10, 160, 112),
        )

        self.assertIsNone(result)

    def test_snap_candidate_target_accepts_compact_control_type_words(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        cases = (
            ("listitem", "listitem", (10, 10, 160, 32)),
            ("treeitem", "treeitem", (10, 10, 160, 32)),
            ("menuitem", "menuitem", (10, 10, 160, 28)),
            ("tabitem", "tabitem", (10, 10, 140, 32)),
            ("headeritem", "headeritem", (10, 10, 140, 28)),
            ("splitbutton", "splitbutton", (10, 10, 160, 32)),
        )
        for word, control_type, rect in cases:
            with self.subTest(word=word):
                result = snap_candidate_target(
                    instruction=f"Click this {word}.",
                    candidates=[
                        ControlCandidate("c001", "Settings", control_type, rect),
                    ],
                    model_rect=rect,
                )

                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.source, "candidate_snap")
                self.assertEqual(result.target_id, "c001")
                self.assertFalse(result.rejected_reason)
                self.assertEqual(result.rect, rect)

    def test_snap_candidate_target_accepts_generic_split_button(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click this split button.",
            candidates=[
                ControlCandidate("c001", "Export", "splitbutton", (10, 10, 160, 32)),
            ],
            model_rect=(10, 10, 160, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c001")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 160, 32))

    def test_snap_candidate_target_rejects_broad_split_button_group(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click this split button.",
            candidates=[
                ControlCandidate("c001", "Export", "splitbutton", (10, 10, 160, 32)),
                ControlCandidate("c002", "Share", "splitbutton", (10, 50, 160, 32)),
                ControlCandidate("c003", "Archive", "splitbutton", (10, 90, 160, 32)),
            ],
            model_rect=(10, 10, 160, 112),
        )

        self.assertIsNone(result)

    def test_snap_candidate_target_accepts_browser_address_bar_wording(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        cases = (
            "Focus the URL bar.",
            "Click the location bar.",
            "Click the omnibox.",
        )
        for instruction in cases:
            with self.subTest(instruction=instruction):
                result = snap_candidate_target(
                    instruction=instruction,
                    candidates=[
                        ControlCandidate("c001", "Address", "edit", (10, 10, 240, 32)),
                    ],
                    model_rect=(10, 10, 240, 32),
                )

                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.source, "candidate_snap")
                self.assertEqual(result.target_id, "c001")
                self.assertFalse(result.rejected_reason)
                self.assertEqual(result.rect, (10, 10, 240, 32))

    def test_snap_candidate_target_prefers_address_edit_in_broad_bar_group(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Focus the URL bar.",
            candidates=[
                ControlCandidate("c001", "Address", "edit", (10, 10, 240, 32)),
                ControlCandidate("c002", "Search", "edit", (10, 50, 240, 32)),
                ControlCandidate("c003", "Filter", "edit", (10, 90, 240, 32)),
            ],
            model_rect=(10, 10, 240, 112),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c001")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 240, 32))

    def test_snap_candidate_target_rejects_text_entry_wording_on_plain_button(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        cases = (
            ("Type your email.", "Email", (10, 10, 180, 32)),
            ("Enter the verification code.", "Verification code", (10, 10, 220, 32)),
            ("Click the search bar.", "Search", (10, 10, 180, 32)),
            ("Click the find bar.", "Find", (10, 10, 180, 32)),
            ("Click the filter bar.", "Filter", (10, 10, 180, 32)),
        )
        for instruction, label, rect in cases:
            with self.subTest(instruction=instruction):
                result = snap_candidate_target(
                    instruction=instruction,
                    candidates=[
                        ControlCandidate("c001", label, "button", rect),
                    ],
                    model_rect=rect,
                )

                self.assertIsNone(result)

    def test_snap_candidate_target_prefers_search_bar_edit_over_same_label_button(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click the search bar.",
            candidates=[
                ControlCandidate("c001", "Search", "edit", (10, 10, 260, 32)),
                ControlCandidate("c002", "Search", "button", (300, 10, 90, 32)),
            ],
            model_rect=(10, 10, 380, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c001")
        self.assertEqual(result.rect, (10, 10, 260, 32))
        self.assertFalse(result.rejected_reason)

    def test_snap_candidate_target_rejects_state_and_choice_wording_on_plain_button(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        cases = (
            ("Check Remember me.", "Remember me"),
            ("Uncheck Remember me.", "Remember me"),
            ("Tick Remember me.", "Remember me"),
            ("Turn on dark mode.", "Dark mode"),
            ("Enable notifications.", "Notifications"),
            ("Pick Daily choice.", "Daily"),
            ("Choose Weekly option.", "Weekly"),
            ("Select Yes.", "Yes"),
            ("Open Country select.", "Country"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction):
                result = snap_candidate_target(
                    instruction=instruction,
                    candidates=[
                        ControlCandidate("c001", label, "button", (10, 10, 180, 32)),
                    ],
                    model_rect=(10, 10, 180, 32),
                )

                self.assertIsNone(result)

    def test_snap_candidate_target_keeps_check_for_updates_button(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Check for updates.",
            candidates=[
                ControlCandidate("c001", "Check for updates", "button", (10, 10, 180, 32)),
            ],
            model_rect=(10, 10, 180, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c001")
        self.assertFalse(result.rejected_reason)

    def test_snap_candidate_target_accepts_button_control_suffix(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click this button control.",
            candidates=[
                ControlCandidate("c001", "Submit", "button", (10, 10, 120, 32)),
            ],
            model_rect=(10, 10, 120, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c001")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 120, 32))

    def test_snap_candidate_target_accepts_literal_edit(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click this edit control.",
            candidates=[
                ControlCandidate("c001", "Search", "edit", (10, 10, 240, 32)),
            ],
            model_rect=(10, 10, 240, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c001")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 240, 32))

    def test_snap_candidate_target_rejects_broad_literal_edit_group(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click this edit.",
            candidates=[
                ControlCandidate("c001", "Search", "edit", (10, 10, 240, 32)),
                ControlCandidate("c002", "Filter", "edit", (10, 50, 240, 32)),
                ControlCandidate("c003", "Name", "edit", (10, 90, 240, 32)),
            ],
            model_rect=(10, 10, 240, 112),
        )

        self.assertIsNone(result)

    def test_snap_candidate_target_accepts_contextual_container_wording(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        cases = (
            ("Click this toolbar button.", "Save", "button", (10, 10, 100, 32)),
            ("Click Toolbar button.", "Toolbar", "button", (10, 10, 100, 32)),
            ("Click this toolbar icon.", "Settings", "button", (10, 10, 32, 32)),
            ("Click this form field.", "Name", "edit", (10, 10, 240, 32)),
            ("Click this dialog button.", "OK", "button", (10, 10, 80, 32)),
            ("Click this modal button.", "OK", "button", (10, 10, 80, 32)),
            ("Click this panel button.", "Save", "button", (10, 10, 100, 32)),
            ("Click Panel button.", "Panel", "button", (10, 10, 100, 32)),
            ("Click this table row.", "Order 123", "listitem", (10, 10, 240, 32)),
            ("Click this grid row.", "Order 123", "listitem", (10, 10, 240, 32)),
            ("Click this page link.", "Docs", "hyperlink", (10, 10, 120, 28)),
            ("Click this card checkbox.", "Done", "checkbox", (10, 10, 160, 32)),
            ("Click this section toggle.", "Dark mode", "checkbox", (10, 10, 160, 32)),
            ("Click this drawer item.", "Settings", "listitem", (10, 10, 160, 32)),
            ("Click this pane button.", "Apply", "button", (10, 10, 100, 32)),
            ("Click this popup menu item.", "Open", "menuitem", (10, 10, 120, 28)),
            ("Click this navigation tab.", "Settings", "tabitem", (10, 10, 140, 32)),
            ("Click this sidebar item.", "Settings", "listitem", (10, 10, 160, 32)),
            ("Click this nav item.", "Settings", "listitem", (10, 10, 160, 32)),
        )
        for instruction, label, control_type, rect in cases:
            with self.subTest(instruction=instruction):
                result = snap_candidate_target(
                    instruction=instruction,
                    candidates=[
                        ControlCandidate("c001", label, control_type, rect),
                    ],
                    model_rect=rect,
                )

                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.source, "candidate_snap")
                self.assertEqual(result.target_id, "c001")
                self.assertFalse(result.rejected_reason)
                self.assertEqual(result.rect, rect)

    def test_snap_candidate_target_rejects_broad_sidebar_item_group(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click this sidebar item.",
            candidates=[
                ControlCandidate("c001", "General", "listitem", (10, 10, 160, 32)),
                ControlCandidate("c002", "Privacy", "listitem", (10, 50, 160, 32)),
                ControlCandidate("c003", "Billing", "listitem", (10, 90, 160, 32)),
            ],
            model_rect=(10, 10, 160, 112),
        )

        self.assertIsNone(result)

    def test_snap_candidate_target_rejects_broad_table_row_group(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click this table row.",
            candidates=[
                ControlCandidate("c001", "Order 1", "listitem", (10, 10, 240, 32)),
                ControlCandidate("c002", "Order 2", "listitem", (10, 50, 240, 32)),
                ControlCandidate("c003", "Order 3", "listitem", (10, 90, 240, 32)),
            ],
            model_rect=(10, 10, 240, 112),
        )

        self.assertIsNone(result)

    def test_snap_candidate_target_allows_toggle_sidebar_button_label(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click Toggle sidebar.",
            candidates=[
                ControlCandidate("c001", "Toggle sidebar", "button", (10, 10, 150, 32)),
                ControlCandidate("c002", "Dark mode", "checkbox", (10, 60, 140, 32)),
            ],
            model_rect=(10, 10, 150, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c001")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 150, 32))

    def test_snap_candidate_target_accepts_generic_column_header(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click this column header.",
            candidates=[
                ControlCandidate("c001", "Status", "headeritem", (100, 50, 120, 28)),
            ],
            model_rect=(100, 50, 120, 28),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c001")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (100, 50, 120, 28))

    def test_snap_candidate_target_rejects_broad_header_row_without_label(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click this column header.",
            candidates=[
                ControlCandidate("c001", "Name", "headeritem", (20, 50, 120, 28)),
                ControlCandidate("c002", "Status", "headeritem", (140, 50, 120, 28)),
                ControlCandidate("c003", "Owner", "headeritem", (260, 50, 120, 28)),
            ],
            model_rect=(20, 50, 360, 28),
        )

        self.assertIsNone(result)

    def test_snap_candidate_target_rejects_checkbox_intent_on_unlabeled_button(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click this checkbox.",
            candidates=[
                ControlCandidate("c001", "", "button", (10, 10, 32, 32)),
            ],
            model_rect=(10, 10, 32, 32),
        )

        self.assertIsNone(result)

    def test_snap_candidate_target_uses_single_checkbox_inside_loose_row(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click this checkbox.",
            candidates=[
                ControlCandidate("c001", "Task row", "listitem", (10, 10, 600, 80)),
                ControlCandidate("c002", "Done", "checkbox", (24, 34, 20, 20)),
            ],
            model_rect=(10, 10, 600, 80),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c002")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (24, 34, 20, 20))

    def test_snap_candidate_target_uses_single_checkbox_inside_contextual_row(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click the checkbox in Task row.",
            candidates=[
                ControlCandidate("c001", "Task row", "listitem", (10, 10, 600, 80)),
                ControlCandidate("c002", "Done", "checkbox", (24, 34, 20, 20)),
            ],
            model_rect=(10, 10, 600, 80),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c002")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (24, 34, 20, 20))

    def test_snap_candidate_target_rejects_multiple_contextual_checkboxes(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click the checkbox in Task row.",
            candidates=[
                ControlCandidate("c001", "Task row", "listitem", (10, 10, 600, 80)),
                ControlCandidate("c002", "Done", "checkbox", (24, 24, 20, 20)),
                ControlCandidate("c003", "Archived", "checkbox", (24, 52, 20, 20)),
            ],
            model_rect=(10, 10, 600, 80),
        )

        self.assertIsNone(result)

    def test_snap_candidate_target_rejects_multiple_checkboxes_inside_loose_row(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click this checkbox.",
            candidates=[
                ControlCandidate("c001", "Task row", "listitem", (10, 10, 600, 80)),
                ControlCandidate("c002", "Done", "checkbox", (24, 24, 20, 20)),
                ControlCandidate("c003", "Archived", "checkbox", (24, 52, 20, 20)),
            ],
            model_rect=(10, 10, 600, 80),
        )

        self.assertIsNone(result)

    def test_snap_candidate_target_ignores_same_visual_duplicate(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click Save.",
            candidates=[
                ControlCandidate("c001", "Save", "button", (100, 100, 50, 24), automation_id="save-a"),
                ControlCandidate("c002", "Save", "button", (100, 100, 50, 24), automation_id="save-b"),
            ],
            model_rect=(96, 96, 60, 30),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c001")

    def test_snap_candidate_target_prefers_foreground_duplicate_when_geometry_is_close(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click Save.",
            candidates=[
                ControlCandidate("c001", "Save", "button", (80, 100, 80, 32), window_rank=2),
                ControlCandidate("c002", "Save", "button", (170, 100, 80, 32), window_rank=0),
            ],
            model_rect=(130, 100, 80, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c002")

    def test_snap_candidate_target_rejects_exact_background_duplicate(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click this button.",
            candidates=[
                ControlCandidate("c001", "Save", "button", (120, 100, 80, 32), window_rank=0),
                ControlCandidate("c002", "Save", "button", (120, 145, 80, 32), window_rank=2),
            ],
            model_rect=(120, 145, 80, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c002")
        self.assertEqual(result.rejected_reason, "ambiguous candidate snap")

    def test_snap_candidate_target_rejects_automation_only_when_visible_alternative_exists(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click Save.",
            candidates=[
                ControlCandidate("c001", "", "button", (10, 10, 32, 32), automation_id="saveButton"),
                ControlCandidate("c002", "Save", "button", (160, 10, 80, 32)),
            ],
            model_rect=(10, 10, 32, 32),
        )

        self.assertIsNone(result)

    def test_snap_candidate_target_rejects_visible_text_conflict_despite_matching_automation_id(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click Save.",
            candidates=[
                ControlCandidate(
                    "c001",
                    "Cancel",
                    "button",
                    (100, 100, 50, 24),
                    automation_id="saveButton",
                )
            ],
            model_rect=(100, 100, 50, 24),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.rejected_reason, "candidate semantic mismatch")

    def test_snap_candidate_target_prefers_tight_search_button_over_field(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target

        candidates = [
            ControlCandidate("field", "Search", "edit", (10, 10, 240, 32)),
            ControlCandidate("btn", "", "button", (220, 12, 28, 28), automation_id="SearchButton"),
        ]

        text_target = resolve_candidate_target(
            target_id="",
            instruction="Click the Search field button.",
            candidates=candidates,
            model_rect=(220, 12, 28, 28),
        )
        snap_target = snap_candidate_target(
            instruction="Click the Search field button.",
            candidates=candidates,
            model_rect=(220, 12, 28, 28),
        )

        self.assertIsNotNone(text_target)
        assert text_target is not None
        self.assertEqual(text_target.source, "text_match")
        self.assertEqual(text_target.target_id, "btn")
        self.assertFalse(text_target.rejected_reason)
        self.assertIsNotNone(snap_target)
        assert snap_target is not None
        self.assertEqual(snap_target.source, "candidate_snap")
        self.assertEqual(snap_target.target_id, "btn")
        self.assertFalse(snap_target.rejected_reason)

    def test_snap_candidate_target_accepts_row_scoped_menu_launcher_button(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target

        candidates = [
            ControlCandidate("row1", "Order 123", "listitem", (100, 100, 500, 48)),
            ControlCandidate("menu1", "More options", "button", (560, 108, 32, 32)),
        ]

        target_id = resolve_candidate_target(
            target_id="menu1",
            instruction="Open menu in row.",
            candidates=candidates,
            model_rect=(560, 108, 32, 32),
        )
        snap_target = snap_candidate_target(
            instruction="Open menu in row.",
            candidates=candidates,
            model_rect=(560, 108, 32, 32),
        )

        self.assertEqual(target_id.target_id, "menu1")
        self.assertFalse(target_id.rejected_reason)
        self.assertEqual(snap_target.target_id, "menu1")
        self.assertFalse(snap_target.rejected_reason)

    def test_snap_candidate_target_accepts_combobox_dropdown_arrow_button(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target

        candidates = [
            ControlCandidate("combo1", "Country", "combobox", (100, 100, 220, 32)),
            ControlCandidate("arrow1", "Open", "button", (292, 100, 28, 32)),
        ]

        arrow_target = resolve_candidate_target(
            target_id="arrow1",
            instruction="Click the down arrow.",
            candidates=candidates,
            model_rect=(292, 100, 28, 32),
        )
        combo_with_arrow = resolve_candidate_target(
            target_id="combo1",
            instruction="Click the down arrow.",
            candidates=candidates,
            model_rect=(100, 100, 220, 32),
        )
        combo_only = resolve_candidate_target(
            target_id="combo1",
            instruction="Click the down arrow.",
            candidates=[candidates[0]],
            model_rect=(100, 100, 220, 32),
        )
        snap_target = snap_candidate_target(
            instruction="Click the down arrow.",
            candidates=candidates,
            model_rect=(292, 100, 28, 32),
        )
        labeled_combo_with_arrow = resolve_candidate_target(
            target_id="combo1",
            instruction="Click the Country dropdown arrow.",
            candidates=candidates,
            model_rect=(100, 100, 220, 32),
        )
        labeled_text_target = resolve_candidate_target(
            target_id="",
            instruction="Click the Country dropdown arrow.",
            candidates=candidates,
            model_rect=(100, 100, 220, 32),
        )
        labeled_snap_target = snap_candidate_target(
            instruction="Click the Country dropdown arrow.",
            candidates=candidates,
            model_rect=(100, 100, 220, 32),
        )

        self.assertEqual(arrow_target.target_id, "arrow1")
        self.assertFalse(arrow_target.rejected_reason)
        self.assertEqual(combo_with_arrow.rejected_reason, "target_id control type mismatch")
        self.assertEqual(combo_only.target_id, "combo1")
        self.assertFalse(combo_only.rejected_reason)
        self.assertEqual(snap_target.target_id, "arrow1")
        self.assertFalse(snap_target.rejected_reason)
        self.assertEqual(
            labeled_combo_with_arrow.rejected_reason,
            "target_id control type mismatch",
        )
        self.assertEqual(labeled_text_target.target_id, "arrow1")
        self.assertFalse(labeled_text_target.rejected_reason)
        self.assertEqual(labeled_snap_target.target_id, "arrow1")
        self.assertFalse(labeled_snap_target.rejected_reason)


class HelpTargetHarnessTests(unittest.TestCase):
    def _capture(self):
        from screen import Capture

        return Capture(
            png_bytes=b"png",
            width=1000,
            height=1000,
            monitor_left=0,
            monitor_top=0,
            scale=1.0,
        )

    def _decision(self, payload: dict):
        from agent import _parse_live_help_decision
        import json

        return _parse_live_help_decision(json.dumps(payload))

    def test_target_id_uses_candidate_rect_not_model_rect(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Save.",
                    "target_id": "c001",
                    "target": {"x": 100, "y": 150, "width": 120, "height": 60},
                }
            ),
            self._capture(),
            [ControlCandidate("c001", "Save", "button", (120, 160, 80, 32))],
        )

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.rect, (120, 160, 80, 32))

    def test_wrong_target_id_recovers_by_text_match(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Save.",
                    "target_id": "c002",
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Save", "button", (120, 160, 80, 32)),
                ControlCandidate("c002", "Cancel", "button", (260, 160, 80, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")

    def test_text_entry_action_wrong_target_id_recovers_to_edit(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Type your email.",
                    "target_id": "c002",
                    "target": {"x": 300, "y": 160, "width": 90, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Email", "edit", (120, 160, 160, 32)),
                ControlCandidate("c002", "Email", "button", (300, 160, 90, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertEqual(target.rect, (120, 160, 160, 32))
        self.assertFalse(target.rejected_reason)

    def test_clipboard_field_request_recovers_to_input_candidate(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate("search", "Search", "edit", (120, 160, 500, 40)),
            ControlCandidate("paste", "Paste", "button", (20, 20, 90, 32)),
            ControlCandidate("clear", "Clear", "button", (586, 166, 28, 28)),
        ]
        instruction = "Paste into the search field."

        for decision in (
            self._decision(
                {
                    "kind": "step",
                    "instruction": instruction,
                    "target_id": "paste",
                    "target": {"x": 20, "y": 20, "width": 90, "height": 32},
                }
            ),
            self._decision(
                {
                    "kind": "step",
                    "instruction": instruction,
                    "target": {"x": 20, "y": 20, "width": 90, "height": 32},
                }
            ),
        ):
            with self.subTest(source=decision.target_id or "rect"):
                target = resolve_help_target(decision, self._capture(), candidates)

                self.assertEqual(target.source, "text_match")
                self.assertEqual(target.target_id, "search")
                self.assertEqual(target.rect, (120, 160, 500, 40))
                self.assertFalse(target.rejected_reason)

        target = resolve_candidate_target(
            target_id="",
            instruction=instruction,
            candidates=candidates,
            model_rect=None,
        )

        self.assertIsNotNone(target)
        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "search")

        message_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Paste into message.",
                    "target_id": "paste",
                    "target": {"x": 20, "y": 20, "width": 90, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("paste", "Paste", "button", (20, 20, 90, 32)),
                ControlCandidate("message", "Message", "edit", (120, 160, 500, 40)),
            ],
        )

        self.assertEqual(message_target.source, "text_match")
        self.assertEqual(message_target.target_id, "message")
        self.assertEqual(message_target.rect, (120, 160, 500, 40))
        self.assertFalse(message_target.rejected_reason)

        for instruction, field_label in (
            ("Paste into chat.", "Chat"),
            ("Paste into the comment.", "Comment"),
            ("Paste into reply.", "Reply"),
        ):
            with self.subTest(destination=field_label):
                destination_target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "paste",
                            "target": {"x": 20, "y": 20, "width": 90, "height": 32},
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate("paste", "Paste", "button", (20, 20, 90, 32)),
                        ControlCandidate("destination", field_label, "edit", (120, 160, 500, 40)),
                    ],
                )

                self.assertEqual(destination_target.source, "text_match")
                self.assertEqual(destination_target.target_id, "destination")
                self.assertEqual(destination_target.rect, (120, 160, 500, 40))
                self.assertFalse(destination_target.rejected_reason)

        for toolbar_instruction in ("Paste selected text.", "Click Paste."):
            with self.subTest(toolbar_instruction=toolbar_instruction):
                toolbar_target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": toolbar_instruction,
                            "target_id": "paste",
                        }
                    ),
                    self._capture(),
                    candidates,
                )

                self.assertEqual(toolbar_target.source, "target_id")
                self.assertEqual(toolbar_target.target_id, "paste")
                self.assertFalse(toolbar_target.rejected_reason)

    def test_search_bar_model_rect_rejects_plain_button_overlay(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click the search bar.",
                    "target": {"x": 300, "y": 160, "width": 90, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Search", "button", (300, 160, 90, 32)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.rejected_reason, "candidate snapshot no match")

    def test_state_action_wrong_target_id_recovers_to_checkbox(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Check Remember me.",
                    "target_id": "c002",
                    "target": {"x": 300, "y": 160, "width": 90, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Remember me", "checkbox", (120, 160, 160, 32)),
                ControlCandidate("c002", "Remember me", "button", (300, 160, 90, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertEqual(target.rect, (120, 160, 160, 32))
        self.assertFalse(target.rejected_reason)

    def test_action_request_recovers_from_passive_text_status_to_checkbox(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Mark as done.",
                    "target_id": "status",
                    "target": {"x": 300, "y": 100, "width": 80, "height": 24},
                }
            ),
            self._capture(),
            [
                ControlCandidate("status", "Done", "text", (300, 100, 80, 24)),
                ControlCandidate("checkbox", "Done", "checkbox", (80, 100, 24, 24)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "checkbox")
        self.assertEqual(target.rect, (80, 100, 24, 24))
        self.assertFalse(target.rejected_reason)

    def test_state_action_model_rect_prefers_matching_action_button_over_noun_checkbox(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Enable notifications.",
                    "target": {"x": 120, "y": 160, "width": 190, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Enable notifications", "button", (120, 160, 190, 32)),
                ControlCandidate("c002", "Notifications", "checkbox", (360, 160, 180, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertEqual(target.rect, (120, 160, 190, 32))
        self.assertFalse(target.rejected_reason)

    def test_choice_wording_wrong_target_id_recovers_to_radio(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            (
                "Pick Daily choice.",
                ControlCandidate("c001", "Daily", "radiobutton", (120, 160, 160, 32)),
                ControlCandidate("c002", "Daily", "button", (300, 160, 90, 32)),
            ),
            (
                "Select Yes.",
                ControlCandidate("c001", "Yes", "radiobutton", (120, 160, 80, 32)),
                ControlCandidate("c002", "Yes", "button", (300, 160, 80, 32)),
            ),
            (
                "Open Country select.",
                ControlCandidate("c001", "Country", "combobox", (120, 160, 180, 32)),
                ControlCandidate("c002", "Country", "button", (300, 160, 100, 32)),
            ),
        )
        for instruction, expected, decoy in cases:
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": decoy.id,
                            "target": {
                                "x": decoy.rect[0],
                                "y": decoy.rect[1],
                                "width": decoy.rect[2],
                                "height": decoy.rect[3],
                            },
                        }
                    ),
                    self._capture(),
                    [expected, decoy],
                )

                self.assertEqual(target.source, "text_match")
                self.assertEqual(target.target_id, expected.id)
                self.assertEqual(target.rect, expected.rect)
                self.assertFalse(target.rejected_reason)

    def test_check_for_updates_model_rect_keeps_button_overlay(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Check for updates.",
                    "target": {"x": 120, "y": 160, "width": 180, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Check for updates", "button", (120, 160, 180, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)

    def test_save_action_target_id_accepts_floppy_disk_button(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Save document.",
                    "target_id": "c001",
                }
            ),
            self._capture(),
            [ControlCandidate("c001", "Floppy disk", "button", (120, 160, 120, 32))],
        )

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.target_id, "c001")
        self.assertEqual(target.rect, (120, 160, 120, 32))
        self.assertFalse(target.rejected_reason)

    def test_floppy_disk_action_target_id_accepts_save_button(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click the floppy disk.",
                    "target_id": "c001",
                }
            ),
            self._capture(),
            [ControlCandidate("c001", "Save", "button", (120, 160, 100, 32))],
        )

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.target_id, "c001")
        self.assertEqual(target.rect, (120, 160, 100, 32))
        self.assertFalse(target.rejected_reason)

    def test_save_symbol_target_id_accepts_icon(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Save document.",
                    "target_id": "c001",
                }
            ),
            self._capture(),
            [ControlCandidate("c001", "\U0001f4be", "button", (120, 160, 32, 32))],
        )

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.target_id, "c001")
        self.assertEqual(target.rect, (120, 160, 32, 32))
        self.assertFalse(target.rejected_reason)

    def test_save_text_match_overrides_cancel_geometry(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Save document.",
                    "target": {"x": 300, "y": 160, "width": 140, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Floppy disk", "button", (120, 160, 120, 32)),
                ControlCandidate("c002", "Cancel", "button", (300, 160, 140, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertEqual(target.rect, (120, 160, 120, 32))
        self.assertFalse(target.rejected_reason)

    def test_unlabeled_target_id_with_geometry_recovers_to_visible_text_match(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Save.",
                    "target_id": "c001",
                    "target": {"x": 10, "y": 10, "width": 32, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "", "button", (10, 10, 32, 32)),
                ControlCandidate("c002", "Save", "button", (100, 10, 60, 30)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c002")
        self.assertEqual(target.rect, (100, 10, 60, 30))
        self.assertFalse(target.rejected_reason)

    def test_model_rect_on_automation_only_candidate_recovers_to_visible_text_match(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Save.",
                    "target": {"x": 10, "y": 10, "width": 32, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "", "button", (10, 10, 32, 32), automation_id="saveButton"),
                ControlCandidate("c002", "Save", "button", (120, 10, 80, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c002")
        self.assertEqual(target.rect, (120, 10, 80, 32))
        self.assertFalse(target.rejected_reason)

    def test_automation_only_target_id_recovers_to_visible_text_match(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Save.",
                    "target_id": "c001",
                    "target": {"x": 10, "y": 10, "width": 32, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "", "button", (10, 10, 32, 32), automation_id="saveButton"),
                ControlCandidate("c002", "Save", "button", (120, 10, 80, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c002")
        self.assertEqual(target.rect, (120, 10, 80, 32))
        self.assertFalse(target.rejected_reason)

    def test_background_target_id_with_geometry_does_not_resnap_same_rejected_target(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Save.",
                    "target_id": "c001",
                    "target": {"x": 10, "y": 10, "width": 60, "height": 30},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Save", "button", (10, 10, 60, 30), window_rank=2),
                ControlCandidate("c002", "Save", "button", (300, 10, 60, 30), window_rank=0),
            ],
        )

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.target_id, "c001")
        self.assertEqual(target.rejected_reason, "target_id ambiguous")

    def test_wrong_target_id_recovers_by_geometry_when_text_is_ambiguous(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Save.",
                    "target_id": "c001",
                    "target": {"x": 120, "y": 160, "width": 80, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Cancel", "button", (20, 160, 80, 32)),
                ControlCandidate("c002", "Save", "button", (120, 160, 80, 32)),
                ControlCandidate("c003", "Save", "button", (260, 160, 80, 32)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.target_id, "c002")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (120, 160, 80, 32))

    def test_ambiguous_text_match_without_target_id_blocks_geometry_snap(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Save.",
                    "target": {"x": 120, "y": 160, "width": 80, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Save", "button", (120, 160, 80, 32)),
                ControlCandidate("c002", "Save", "button", (520, 160, 80, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertEqual(target.rejected_reason, "ambiguous text match")

    def test_unknown_target_id_without_rect_downgrades_no_overlay(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Save.",
                    "target_id": "c999",
                }
            ),
            self._capture(),
            [ControlCandidate("c001", "Save", "button", (120, 160, 80, 32))],
        )

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.rejected_reason, "unknown target_id")

    def test_unknown_target_id_with_rect_does_not_fall_back_to_raw_model_rect(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target
        from rect_snap import SnapResult

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Save.",
                    "target_id": "c999",
                    "target": {"x": 400, "y": 400, "width": 70, "height": 30},
                }
            ),
            self._capture(),
            [ControlCandidate("c001", "Cancel", "button", (120, 160, 80, 32))],
            snapper=lambda rect, _instruction: SnapResult(rect=rect, confidence=0.0, source="model"),
        )

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.rejected_reason, "unknown target_id")

    def test_model_rect_snaps_to_candidate_snapshot_without_fresh_uia(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        calls: list[bool] = []

        def snapper(_rect, _instruction):
            calls.append(True)
            raise AssertionError("fresh UIA snapper should not be called")

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click this button.",
                    "target": {"x": 105, "y": 155, "width": 105, "height": 50},
                }
            ),
            self._capture(),
            [ControlCandidate("c001", "Save", "button", (120, 160, 80, 32))],
            snapper=snapper,
        )

        self.assertFalse(calls)
        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.rect, (120, 160, 80, 32))

    def test_file_picker_fresh_snap_rejects_taskbar_file_explorer_button(self) -> None:
        from help_session import resolve_help_target
        from rect_snap import snap_to_control

        file_explorer = _make_button("File Explorer pinned", 120, 160, 180, 32)
        desktop = _FakeDesktop([
            _make_window("Taskbar", 0, 140, 800, 80, [file_explorer])
        ])

        def snapper(rect, instruction):
            return snap_to_control(
                rect,
                instruction,
                desktop_factory=lambda: desktop,
                timeout_ms=2000,
            )

        for instruction in (
            "Open the file picker.",
            "Attach file.",
            "Upload a file.",
            "Choose a file.",
        ):
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target": {
                                "x": 120,
                                "y": 160,
                                "width": 180,
                                "height": 32,
                            },
                        }
                    ),
                    self._capture(),
                    [],
                    snapper=snapper,
                )

                self.assertEqual(target.source, "snap")
                self.assertEqual(target.rect, (120, 160, 180, 32))
                self.assertEqual(target.rejected_reason, "candidate semantic mismatch")

    def test_file_explorer_fresh_snap_still_accepts_taskbar_file_explorer_button(self) -> None:
        from help_session import resolve_help_target
        from rect_snap import snap_to_control

        file_explorer = _make_button("File Explorer pinned", 120, 160, 180, 32)
        desktop = _FakeDesktop([
            _make_window("Taskbar", 0, 140, 800, 80, [file_explorer])
        ])

        def snapper(rect, instruction):
            return snap_to_control(
                rect,
                instruction,
                desktop_factory=lambda: desktop,
                timeout_ms=2000,
            )

        for instruction in ("Open File Explorer.", "Click File Explorer."):
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target": {
                                "x": 120,
                                "y": 160,
                                "width": 180,
                                "height": 32,
                            },
                        }
                    ),
                    self._capture(),
                    [],
                    snapper=snapper,
                )

                self.assertEqual(target.source, "snap")
                self.assertEqual(target.rect, (120, 160, 180, 32))
                self.assertFalse(target.rejected_reason)

    def test_loose_row_model_rect_snaps_to_tight_child_action(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Settings.",
                    "target": {"x": 10, "y": 10, "width": 600, "height": 80},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Settings", "listitem", (10, 10, 600, 80)),
                ControlCandidate("c002", "Settings", "button", (20, 20, 70, 30)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c002")
        self.assertEqual(target.rect, (20, 20, 70, 30))
        self.assertFalse(target.rejected_reason)

    def test_row_target_id_recovers_to_tight_child_action(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Settings.",
                    "target_id": "c001",
                    "target": {"x": 10, "y": 10, "width": 600, "height": 80},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Settings", "listitem", (10, 10, 600, 80)),
                ControlCandidate("c002", "Settings", "button", (20, 20, 70, 30)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c002")
        self.assertEqual(target.rect, (20, 20, 70, 30))
        self.assertFalse(target.rejected_reason)

    def test_broad_same_type_action_target_recovers_to_tight_child(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate("c001", "Settings", "button", (20, 80, 600, 80)),
            ControlCandidate("c002", "Settings", "button", (540, 104, 70, 28)),
        ]
        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Settings.",
                    "target_id": "c001",
                    "target": {"x": 20, "y": 80, "width": 600, "height": 80},
                }
            ),
            self._capture(),
            candidates,
        )
        snap_target = snap_candidate_target(
            instruction="Click Settings.",
            candidates=candidates,
            model_rect=(20, 80, 600, 80),
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c002")
        self.assertEqual(target.rect, (540, 104, 70, 28))
        self.assertFalse(target.rejected_reason)
        self.assertEqual(snap_target.target_id, "c002")
        self.assertFalse(snap_target.rejected_reason)

    def test_broad_surface_action_target_recovers_to_tight_child(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            (
                "Click Settings.",
                [
                    ControlCandidate("c001", "Settings", "window", (20, 20, 500, 400)),
                    ControlCandidate("c002", "Settings", "button", (450, 30, 60, 28)),
                ],
                (450, 30, 60, 28),
            ),
            (
                "Click Save.",
                [
                    ControlCandidate("c001", "Save", "toolbar", (20, 20, 300, 60)),
                    ControlCandidate("c002", "Save", "button", (250, 34, 60, 28)),
                ],
                (250, 34, 60, 28),
            ),
        )
        for instruction, candidates, expected_rect in cases:
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                            "target": {
                                "x": candidates[0].rect[0],
                                "y": candidates[0].rect[1],
                                "width": candidates[0].rect[2],
                                "height": candidates[0].rect[3],
                            },
                        }
                    ),
                    self._capture(),
                    candidates,
                )

                self.assertEqual(target.source, "text_match")
                self.assertEqual(target.target_id, "c002")
                self.assertEqual(target.rect, expected_rect)
                self.assertFalse(target.rejected_reason)

        explicit_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Save toolbar.",
                    "target_id": "c001",
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Save", "toolbar", (20, 20, 300, 60)),
                ControlCandidate("c002", "Save", "button", (250, 34, 60, 28)),
            ],
        )

        self.assertEqual(explicit_target.source, "target_id")
        self.assertEqual(explicit_target.target_id, "c001")
        self.assertFalse(explicit_target.rejected_reason)

    def test_splitbutton_target_id_recovers_to_menu_segment(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open the Export menu.",
                    "target_id": "c001",
                    "target": {"x": 100, "y": 100, "width": 180, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Export", "splitbutton", (100, 100, 180, 32)),
                ControlCandidate("c002", "Export", "button", (100, 100, 140, 32)),
                ControlCandidate("c003", "Export menu", "menuitem", (240, 100, 40, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c003")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (240, 100, 40, 32))

    def test_splitbutton_model_rect_highlights_menu_segment(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open the Export menu.",
                    "target": {"x": 100, "y": 100, "width": 180, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Export", "splitbutton", (100, 100, 180, 32)),
                ControlCandidate("c002", "Export", "button", (100, 100, 140, 32)),
                ControlCandidate("c003", "Export menu", "menuitem", (240, 100, 40, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c003")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (240, 100, 40, 32))

    def test_menu_launcher_target_id_highlights_button(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open the overflow menu.",
                    "target_id": "c001",
                    "target": {"x": 120, "y": 160, "width": 120, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "More options", "button", (120, 160, 120, 32)),
            ],
        )

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (120, 160, 120, 32))

    def test_menu_launcher_model_rect_highlights_button(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click the three dots menu.",
                    "target": {"x": 120, "y": 160, "width": 120, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "More options", "button", (120, 160, 120, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (120, 160, 120, 32))

    def test_contextual_menu_wording_highlights_launcher_button(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Open the profile menu.", "Profile"),
            ("Open the profile menu.", "Person"),
            ("Open the account menu.", "Account"),
            ("Open the account menu.", "Person"),
            ("Open the user menu.", "User"),
            ("Open the user menu.", "Person"),
            ("Open the settings menu.", "Settings"),
            ("Open the account dropdown.", "Account"),
            ("Open the profile drop down.", "Profile"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target": {"x": 120, "y": 160, "width": 120, "height": 32},
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate("c001", label, "button", (120, 160, 120, 32)),
                    ],
                )

                self.assertEqual(target.source, "text_match")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, (120, 160, 120, 32))

    def test_profile_menu_target_id_accepts_person_labels_and_icons(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Open profile.", "Person", (120, 160, 100, 32)),
            ("Open account.", "\U0001f464", (120, 160, 32, 32)),
            ("Open user menu.", "\U0001f465", (120, 160, 32, 32)),
        )
        for instruction, label, rect in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [ControlCandidate("c001", label, "button", rect)],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, rect)

    def test_profile_menu_target_id_accepts_compact_chrome_profile_name(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            "Open profile.",
            "Open profile menu.",
            "Open account dropdown.",
            "Open user menu.",
        )
        for instruction in cases:
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            "Abel (All)",
                            "button",
                            (120, 160, 34, 34),
                            automation_id="view_1018",
                            window_title="about:blank - Google Chrome",
                        ),
                    ],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, (120, 160, 34, 34))

    def test_profile_menu_person_icon_text_match_overrides_settings_geometry(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open profile.",
                    "target": {"x": 300, "y": 160, "width": 120, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "\U0001f464", "button", (120, 160, 32, 32)),
                ControlCandidate("c002", "Settings", "button", (300, 160, 120, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (120, 160, 32, 32))

    def test_profile_menu_chrome_profile_name_overrides_extensions_geometry(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open profile menu.",
                    "target": {"x": 300, "y": 160, "width": 90, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate(
                    "c001",
                    "Abel (All)",
                    "button",
                    (120, 160, 34, 34),
                    automation_id="view_1018",
                    window_title="about:blank - Google Chrome",
                ),
                ControlCandidate(
                    "c002",
                    "Extensions",
                    "button",
                    (300, 160, 90, 32),
                    window_title="about:blank - Google Chrome",
                ),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (120, 160, 34, 34))

    def test_profile_page_prefers_page_link_over_browser_profile_button_geometry(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open profile page.",
                    "target": {"x": 700, "y": 80, "width": 40, "height": 36},
                }
            ),
            self._capture(),
            [
                ControlCandidate(
                    "c001",
                    "Profile 1",
                    "button",
                    (700, 80, 40, 36),
                    window_title="about:blank - Google Chrome",
                ),
                ControlCandidate("c002", "Profile page", "hyperlink", (100, 250, 120, 28)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c002")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (100, 250, 120, 28))

    def test_profile_page_rejects_browser_profile_button_target_id_and_snap(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate(
                "c001",
                "Profile 1",
                "button",
                (700, 80, 40, 36),
                window_title="about:blank - Google Chrome",
            )
        ]
        for instruction in ("Open profile page.", "Click the profile page button."):
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    candidates,
                )
                text_target = resolve_candidate_target(
                    target_id="",
                    instruction=instruction,
                    candidates=candidates,
                )
                snap_target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target": {"x": 700, "y": 80, "width": 40, "height": 36},
                        }
                    ),
                    self._capture(),
                    candidates,
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, "target_id semantic mismatch")
                self.assertIsNone(text_target)
                self.assertEqual(snap_target.source, "candidate_snap")
                self.assertEqual(snap_target.target_id, "c001")
                self.assertEqual(snap_target.rejected_reason, "candidate semantic mismatch")

    def test_profile_name_inference_stays_contextual_to_browser_profile_buttons(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ControlCandidate(
                "c001",
                "Abel (All)",
                "button",
                (120, 160, 34, 34),
                window_title="Contacts",
            ),
            ControlCandidate(
                "c001",
                "All Bookmarks",
                "button",
                (120, 160, 160, 32),
                window_title="about:blank - Google Chrome",
            ),
            ControlCandidate(
                "c001",
                "Unnamed bookmark for "
                "https://gemini.google.com/app?utm_source=app_launcher"
                "&utm_medium=owned&utm_campaign=base_all",
                "button",
                (120, 160, 28, 28),
                window_title="about:blank - Google Chrome",
            ),
        )
        for candidate in cases:
            with self.subTest(label=candidate.text, window=candidate.window_title):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": "Open profile menu.",
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [candidate],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, "target_id semantic mismatch")

    def test_bare_all_rejects_browser_profile_all_hint(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open all.",
                    "target_id": "c001",
                }
            ),
            self._capture(),
            [
                ControlCandidate(
                    "c001",
                    "Abel (All)",
                    "button",
                    (120, 160, 34, 34),
                    window_title="about:blank - Google Chrome",
                ),
            ],
        )

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.target_id, "c001")
        self.assertEqual(target.rejected_reason, "target_id semantic mismatch")

    def test_profile_request_rejects_plain_browser_identity_buttons(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            (
                "Open Chrome profile.",
                ControlCandidate(
                    "c001",
                    "Chrome",
                    "button",
                    (120, 160, 48, 32),
                    window_title="about:blank - Google Chrome",
                ),
            ),
            (
                "Open profile.",
                ControlCandidate(
                    "c001",
                    "Chrome",
                    "button",
                    (120, 160, 48, 32),
                    window_title="about:blank - Google Chrome",
                ),
            ),
            (
                "Open account.",
                ControlCandidate(
                    "c001",
                    "Google Chrome - 5 running windows",
                    "button",
                    (120, 160, 180, 32),
                    window_title="Taskbar",
                ),
            ),
        )
        for instruction, candidate in cases:
            with self.subTest(instruction=instruction, label=candidate.text):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [candidate],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, "target_id semantic mismatch")

    def test_profile_request_recovers_from_plain_chrome_button_to_profile_button(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open Chrome profile.",
                    "target_id": "c001",
                    "target": {"x": 260, "y": 160, "width": 48, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate(
                    "c001",
                    "Chrome",
                    "button",
                    (260, 160, 48, 32),
                    window_title="about:blank - Google Chrome",
                ),
                ControlCandidate(
                    "c002",
                    "Abel (All)",
                    "button",
                    (120, 160, 34, 34),
                    automation_id="view_1018",
                    window_title="about:blank - Google Chrome",
                ),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c002")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (120, 160, 34, 34))

    def test_chrome_profile_menu_rejects_page_profile_button(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate(
                "page_profile",
                "Profile",
                "button",
                (420, 180, 110, 32),
                window_title="Dashboard - Google Chrome",
            ),
            ControlCandidate(
                "chrome_profile",
                "Abel (All)",
                "button",
                (936, 20, 32, 32),
                automation_id="view_1018",
                window_title="Dashboard - Google Chrome",
            ),
        ]
        instruction = "Open Chrome profile menu."

        wrong_target = resolve_candidate_target(
            target_id="page_profile",
            instruction=instruction,
            candidates=candidates,
            model_rect=(420, 180, 110, 32),
        )
        text_target = resolve_candidate_target(
            target_id="",
            instruction=instruction,
            candidates=candidates,
            model_rect=(420, 180, 110, 32),
        )
        snap_target = snap_candidate_target(
            instruction=instruction,
            candidates=candidates,
            model_rect=(420, 180, 110, 32),
        )
        help_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": instruction,
                    "target_id": "page_profile",
                    "target": {"x": 420, "y": 180, "width": 110, "height": 32},
                }
            ),
            self._capture(),
            candidates,
        )

        self.assertEqual(wrong_target.target_id, "page_profile")
        self.assertEqual(wrong_target.rejected_reason, "target_id semantic mismatch")
        for resolved in (text_target, help_target):
            self.assertEqual(resolved.target_id, "chrome_profile")
            self.assertFalse(resolved.rejected_reason)
            self.assertEqual(resolved.rect, (936, 20, 32, 32))
        if snap_target is not None:
            self.assertTrue(snap_target.rejected_reason)

    def test_plain_chrome_and_edit_profile_targets_still_work(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            (
                "Open Chrome.",
                ControlCandidate(
                    "c001",
                    "Chrome",
                    "button",
                    (120, 160, 48, 32),
                    window_title="about:blank - Google Chrome",
                ),
            ),
            (
                "Edit profile.",
                ControlCandidate("c001", "Pencil", "button", (120, 160, 90, 32)),
            ),
        )
        for instruction, candidate in cases:
            with self.subTest(instruction=instruction, label=candidate.text):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [candidate],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)

    def test_contextual_menu_item_wording_still_highlights_menuitem(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open the profile menu item.",
                    "target": {"x": 120, "y": 160, "width": 240, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Profile", "button", (120, 160, 120, 32)),
                ControlCandidate("c002", "Profile", "menuitem", (120, 210, 240, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c002")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (120, 210, 240, 32))

    def test_common_alias_model_rect_highlights_button(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Confirm.",
                    "target": {"x": 120, "y": 160, "width": 80, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "OK", "button", (120, 160, 80, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (120, 160, 80, 32))

    def test_symbol_only_question_mark_target_id_accepts_without_model_rect(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click the question mark.",
                    "target_id": "c001",
                }
            ),
            self._capture(),
            [ControlCandidate("c001", "?", "button", (120, 160, 32, 32))],
        )

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (120, 160, 32, 32))

    def test_symbol_only_text_match_overrides_wrong_model_geometry(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click the question mark.",
                    "target": {"x": 220, "y": 160, "width": 80, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "?", "button", (120, 160, 32, 32)),
                ControlCandidate("c002", "Help", "button", (220, 160, 80, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (120, 160, 32, 32))

    def test_symbol_only_action_buttons_match_instruction_text(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Add a new item.", "+", (120, 160, 32, 32)),
            ("Open the more options menu.", "...", (120, 160, 32, 32)),
            ("Close the dialog.", "\u00d7", (120, 160, 32, 32)),
            ("Open settings.", "\u2699", (120, 160, 32, 32)),
            ("Search.", "\U0001f50d", (120, 160, 32, 32)),
        )
        for instruction, text, rect in cases:
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [ControlCandidate("c001", text, "button", rect)],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, rect)

    def test_generic_settings_target_id_rejects_unnamed_url_bookmark(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        bookmark = (
            "Unnamed bookmark for "
            "https://platform.openai.com/settings/organization/billing/overview"
        )
        for instruction in (
            "Open settings.",
            "Open browser settings.",
            "Open Chrome settings.",
            "Open site settings.",
        ):
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            bookmark,
                            "button",
                            (120, 160, 220, 32),
                            window_title="about:blank - Google Chrome",
                        )
                    ],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, "target_id semantic mismatch")

    def test_generic_settings_downloads_history_and_extensions_reject_browser_tab_title(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from help_session import resolve_help_target

        cases = (
            (
                "Open settings.",
                ControlCandidate(
                    "tab",
                    "Settings - Google Chrome",
                    "tabitem",
                    (80, 0, 220, 40),
                    window_title="Settings - Google Chrome",
                ),
                ControlCandidate(
                    "button",
                    "Settings",
                    "button",
                    (500, 120, 100, 32),
                    window_title="Settings - Google Chrome",
                ),
            ),
            (
                "Open downloads.",
                ControlCandidate(
                    "tab",
                    "Downloads - Google Chrome",
                    "tabitem",
                    (80, 0, 220, 40),
                    window_title="Downloads - Google Chrome",
                ),
                ControlCandidate(
                    "button",
                    "Downloads",
                    "button",
                    (500, 120, 120, 32),
                    window_title="Downloads - Google Chrome",
                ),
            ),
            (
                "Open history.",
                ControlCandidate(
                    "tab",
                    "History - Google Chrome",
                    "tabitem",
                    (80, 0, 220, 40),
                    window_title="History - Google Chrome",
                ),
                ControlCandidate(
                    "button",
                    "History",
                    "button",
                    (500, 120, 100, 32),
                    automation_id="history",
                    window_title="History - Google Chrome",
                ),
            ),
            (
                "Open extensions.",
                ControlCandidate(
                    "tab",
                    "Extensions - Google Chrome",
                    "tabitem",
                    (80, 0, 240, 40),
                    window_title="Extensions - Google Chrome",
                ),
                ControlCandidate(
                    "button",
                    "Extensions",
                    "button",
                    (500, 120, 120, 32),
                    automation_id="extensions",
                    window_title="Extensions - Google Chrome",
                ),
            ),
        )
        for instruction, tab, button in cases:
            candidates = [tab, button]
            with self.subTest(instruction=instruction):
                target_id = resolve_candidate_target(
                    target_id="tab",
                    instruction=instruction,
                    candidates=candidates,
                    model_rect=tab.rect,
                )
                text_target = resolve_candidate_target(
                    target_id="",
                    instruction=instruction,
                    candidates=candidates,
                    model_rect=button.rect,
                )
                snap_target = snap_candidate_target(
                    instruction=instruction,
                    candidates=candidates,
                    model_rect=tab.rect,
                )
                help_target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "tab",
                            "target": {"x": tab.rect[0], "y": tab.rect[1], "width": tab.rect[2], "height": tab.rect[3]},
                        }
                    ),
                    self._capture(),
                    candidates,
                )

                self.assertEqual(target_id.rejected_reason, "target_id semantic mismatch")
                self.assertEqual(text_target.target_id, "button")
                self.assertFalse(text_target.rejected_reason)
                self.assertEqual(snap_target.rejected_reason, "candidate semantic mismatch")
                self.assertEqual(help_target.target_id, "button")
                self.assertFalse(help_target.rejected_reason)

    def test_generic_settings_prefers_visible_settings_over_chrome_menu(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target
        from help_session import resolve_help_target

        menu = ControlCandidate(
            "menu",
            "Chrome",
            "button",
            (960, 8, 32, 32),
            window_title="Settings - Google Chrome",
        )
        settings = ControlCandidate(
            "settings",
            "Settings",
            "button",
            (500, 120, 100, 32),
            window_title="Settings - Google Chrome",
        )
        candidates = [menu, settings]

        target_id = resolve_candidate_target(
            target_id="menu",
            instruction="Open settings.",
            candidates=candidates,
            model_rect=menu.rect,
        )
        text_target = resolve_candidate_target(
            target_id="",
            instruction="Open settings.",
            candidates=candidates,
            model_rect=menu.rect,
        )
        help_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open settings.",
                    "target_id": "menu",
                    "target": {
                        "x": menu.rect[0],
                        "y": menu.rect[1],
                        "width": menu.rect[2],
                        "height": menu.rect[3],
                    },
                }
            ),
            self._capture(),
            candidates,
        )

        self.assertEqual(target_id.rejected_reason, "target_id semantic mismatch")
        self.assertEqual(text_target.target_id, "settings")
        self.assertFalse(text_target.rejected_reason)
        self.assertEqual(help_target.target_id, "settings")
        self.assertFalse(help_target.rejected_reason)

    def test_generic_settings_prefers_visible_settings_over_edge_menu(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from help_session import resolve_help_target

        menu = ControlCandidate(
            "edge_menu",
            "Settings and more",
            "button",
            (930, 8, 42, 34),
            window_title="Dashboard - Microsoft Edge",
        )
        settings = ControlCandidate(
            "page_settings",
            "Settings",
            "button",
            (100, 200, 100, 32),
            window_title="Dashboard - Microsoft Edge",
        )
        candidates = [menu, settings]
        instruction = "Open settings."

        target_id = resolve_candidate_target(
            target_id="edge_menu",
            instruction=instruction,
            candidates=candidates,
            model_rect=menu.rect,
        )
        text_target = resolve_candidate_target(
            target_id="",
            instruction=instruction,
            candidates=candidates,
            model_rect=menu.rect,
        )
        snap_target = snap_candidate_target(
            instruction=instruction,
            candidates=candidates,
            model_rect=menu.rect,
        )
        help_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": instruction,
                    "target_id": "edge_menu",
                    "target": {
                        "x": menu.rect[0],
                        "y": menu.rect[1],
                        "width": menu.rect[2],
                        "height": menu.rect[3],
                    },
                }
            ),
            self._capture(),
            candidates,
        )

        self.assertEqual(target_id.rejected_reason, "target_id semantic mismatch")
        self.assertEqual(text_target.target_id, "page_settings")
        self.assertFalse(text_target.rejected_reason)
        self.assertEqual(snap_target.target_id, "edge_menu")
        self.assertEqual(snap_target.rejected_reason, "candidate semantic mismatch")
        self.assertEqual(help_target.target_id, "page_settings")
        self.assertFalse(help_target.rejected_reason)

    def test_browser_chrome_controls_do_not_steal_app_local_targets(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            (
                "Open the Reports tab in the app.",
                ControlCandidate(
                    "c001",
                    "Reports - MyApp",
                    "tabitem",
                    (80, 0, 220, 40),
                    window_title="MyApp - Google Chrome",
                ),
                ControlCandidate(
                    "c002",
                    "Reports",
                    "tabitem",
                    (260, 110, 120, 32),
                    window_title="MyApp - Google Chrome",
                ),
            ),
            (
                "Open Downloads in the sidebar.",
                ControlCandidate(
                    "c001",
                    "Downloads",
                    "button",
                    (880, 8, 42, 34),
                    automation_id="downloads",
                    window_title="Reports - Google Chrome",
                ),
                ControlCandidate(
                    "c002",
                    "Downloads",
                    "listitem",
                    (120, 160, 180, 32),
                    window_title="Reports - Google Chrome",
                ),
            ),
            (
                "Click Downloads in the workspace.",
                ControlCandidate(
                    "c001",
                    "Downloads",
                    "button",
                    (900, 8, 80, 34),
                    automation_id="downloads",
                    window_title="Project - Google Chrome",
                ),
                ControlCandidate(
                    "c002",
                    "Downloads",
                    "button",
                    (420, 180, 120, 32),
                    window_title="Project - Google Chrome",
                ),
            ),
            (
                "Click Downloads in sidebars.",
                ControlCandidate(
                    "c001",
                    "Downloads",
                    "button",
                    (900, 8, 80, 34),
                    automation_id="downloads",
                    window_title="Project - Google Chrome",
                ),
                ControlCandidate(
                    "c002",
                    "Downloads",
                    "button",
                    (420, 180, 120, 32),
                    window_title="Project - Google Chrome",
                ),
            ),
            (
                "Add favorite to item in the app.",
                ControlCandidate(
                    "c001",
                    "Add to favorites",
                    "button",
                    (910, 8, 42, 34),
                    window_title="Catalog - Microsoft Edge",
                ),
                ControlCandidate(
                    "c002",
                    "Add favorite",
                    "button",
                    (420, 180, 130, 32),
                    window_title="Catalog - Microsoft Edge",
                ),
            ),
            (
                "Open Collections in the app.",
                ControlCandidate(
                    "c001",
                    "Collections",
                    "button",
                    (904, 8, 42, 34),
                    automation_id="Collections",
                    window_title="CRM - Microsoft Edge",
                ),
                ControlCandidate(
                    "c002",
                    "Collections",
                    "listitem",
                    (120, 160, 180, 32),
                    window_title="CRM - Microsoft Edge",
                ),
            ),
            (
                "Open Reading list in the app.",
                ControlCandidate(
                    "c001",
                    "Reading list",
                    "button",
                    (904, 8, 42, 34),
                    automation_id="ReadingList",
                    window_title="CRM - Microsoft Edge",
                ),
                ControlCandidate(
                    "c002",
                    "Reading list",
                    "listitem",
                    (120, 160, 180, 32),
                    window_title="CRM - Microsoft Edge",
                ),
            ),
            (
                "Open Copilot in the app.",
                ControlCandidate(
                    "c001",
                    "Copilot",
                    "button",
                    (904, 8, 42, 34),
                    automation_id="Copilot",
                    window_title="CRM - Microsoft Edge",
                ),
                ControlCandidate(
                    "c002",
                    "Copilot",
                    "listitem",
                    (120, 160, 180, 32),
                    window_title="CRM - Microsoft Edge",
                ),
            ),
            (
                "Open Passwords in the app.",
                ControlCandidate(
                    "c001",
                    "Passwords",
                    "button",
                    (904, 8, 42, 34),
                    automation_id="Passwords",
                    window_title="CRM - Microsoft Edge",
                ),
                ControlCandidate(
                    "c002",
                    "Passwords",
                    "listitem",
                    (120, 160, 180, 32),
                    window_title="CRM - Microsoft Edge",
                ),
            ),
            (
                "Open Browser essentials in the app sidebar.",
                ControlCandidate(
                    "c001",
                    "Browser essentials",
                    "button",
                    (900, 8, 80, 34),
                    automation_id="browseressentials",
                    window_title="Dashboard - Google Chrome",
                ),
                ControlCandidate(
                    "c002",
                    "Browser essentials",
                    "listitem",
                    (120, 180, 180, 36),
                    window_title="Dashboard - Google Chrome",
                ),
            ),
            (
                "Open New tab in the app.",
                ControlCandidate(
                    "c001",
                    "New tab",
                    "button",
                    (904, 8, 42, 34),
                    window_title="CRM - Microsoft Edge",
                ),
                ControlCandidate(
                    "c002",
                    "New tab",
                    "listitem",
                    (120, 160, 180, 32),
                    window_title="CRM - Microsoft Edge",
                ),
            ),
            (
                "Open Search tabs in the app.",
                ControlCandidate(
                    "c001",
                    "Search tabs",
                    "button",
                    (904, 8, 82, 34),
                    window_title="CRM - Microsoft Edge",
                ),
                ControlCandidate(
                    "c002",
                    "Search tabs",
                    "listitem",
                    (120, 160, 180, 32),
                    window_title="CRM - Microsoft Edge",
                ),
            ),
            (
                "Open Drop in the app.",
                ControlCandidate(
                    "c001",
                    "Downloads",
                    "button",
                    (904, 8, 42, 34),
                    automation_id="downloads",
                    window_title="CRM - Microsoft Edge",
                ),
                ControlCandidate(
                    "c002",
                    "Drop",
                    "listitem",
                    (120, 160, 180, 32),
                    window_title="CRM - Microsoft Edge",
                ),
            ),
            (
                "Open Text in the app.",
                ControlCandidate(
                    "c001",
                    "Downloads",
                    "button",
                    (904, 8, 42, 34),
                    automation_id="downloads",
                    window_title="CRM - Microsoft Edge",
                ),
                ControlCandidate(
                    "c002",
                    "Text",
                    "listitem",
                    (120, 160, 180, 32),
                    window_title="CRM - Microsoft Edge",
                ),
            ),
            (
                "Open Field in the app.",
                ControlCandidate(
                    "c001",
                    "Downloads",
                    "button",
                    (904, 8, 42, 34),
                    automation_id="downloads",
                    window_title="CRM - Microsoft Edge",
                ),
                ControlCandidate(
                    "c002",
                    "Field",
                    "listitem",
                    (120, 160, 180, 32),
                    window_title="CRM - Microsoft Edge",
                ),
            ),
            (
                "Open Button in the app.",
                ControlCandidate(
                    "c001",
                    "Downloads",
                    "button",
                    (904, 8, 42, 34),
                    automation_id="downloads",
                    window_title="CRM - Microsoft Edge",
                ),
                ControlCandidate(
                    "c002",
                    "Button",
                    "listitem",
                    (120, 160, 180, 32),
                    window_title="CRM - Microsoft Edge",
                ),
            ),
            (
                "Open Link in the app.",
                ControlCandidate(
                    "c001",
                    "Downloads",
                    "button",
                    (904, 8, 42, 34),
                    automation_id="downloads",
                    window_title="CRM - Microsoft Edge",
                ),
                ControlCandidate(
                    "c002",
                    "Link",
                    "listitem",
                    (120, 160, 180, 32),
                    window_title="CRM - Microsoft Edge",
                ),
            ),
            (
                "Open Input in the app.",
                ControlCandidate(
                    "c001",
                    "Downloads",
                    "button",
                    (904, 8, 42, 34),
                    automation_id="downloads",
                    window_title="CRM - Microsoft Edge",
                ),
                ControlCandidate(
                    "c002",
                    "Input",
                    "listitem",
                    (120, 160, 180, 32),
                    window_title="CRM - Microsoft Edge",
                ),
            ),
            (
                "Open Home in the sidebar.",
                ControlCandidate(
                    "c001",
                    "Home",
                    "button",
                    (96, 8, 34, 34),
                    automation_id="home",
                    window_title="Reports - Google Chrome",
                ),
                ControlCandidate(
                    "c002",
                    "Home",
                    "listitem",
                    (120, 160, 180, 32),
                    window_title="Reports - Google Chrome",
                ),
            ),
            (
                "Refresh the dashboard widget.",
                ControlCandidate(
                    "c001",
                    "Reload",
                    "button",
                    (96, 8, 34, 34),
                    automation_id="reload",
                    window_title="Dashboard - Google Chrome",
                ),
                ControlCandidate(
                    "c002",
                    "Refresh",
                    "button",
                    (420, 220, 100, 32),
                    window_title="Dashboard - Google Chrome",
                ),
            ),
            (
                "Refresh the dashboard widget.",
                ControlCandidate(
                    "c001",
                    "Reload",
                    "button",
                    (96, 108, 34, 34),
                    window_title="Dashboard - Google Chrome",
                ),
                ControlCandidate(
                    "c002",
                    "Refresh",
                    "button",
                    (420, 320, 100, 32),
                    window_title="Dashboard - Google Chrome",
                ),
            ),
            (
                "Refresh the chart.",
                ControlCandidate(
                    "c001",
                    "Reload",
                    "button",
                    (96, 8, 34, 34),
                    automation_id="reload",
                    window_title="Dashboard - Google Chrome",
                ),
                ControlCandidate(
                    "c002",
                    "Refresh",
                    "button",
                    (420, 220, 100, 32),
                    window_title="Dashboard - Google Chrome",
                ),
            ),
            (
                "Refresh the table.",
                ControlCandidate(
                    "c001",
                    "Reload",
                    "button",
                    (96, 8, 34, 34),
                    automation_id="reload",
                    window_title="Dashboard - Google Chrome",
                ),
                ControlCandidate(
                    "c002",
                    "Refresh",
                    "button",
                    (420, 220, 100, 32),
                    window_title="Dashboard - Google Chrome",
                ),
            ),
            (
                "Open Profile in the app.",
                ControlCandidate(
                    "c001",
                    "All",
                    "button",
                    (936, 8, 32, 32),
                    window_title="Dashboard - Google Chrome",
                ),
                ControlCandidate(
                    "c002",
                    "Profile",
                    "button",
                    (420, 180, 110, 32),
                    window_title="Dashboard - Google Chrome",
                ),
            ),
            (
                "Open site info in the app.",
                ControlCandidate(
                    "c001",
                    "site_info_lock",
                    "button",
                    (90, 8, 28, 34),
                    window_title="Dashboard - Google Chrome",
                ),
                ControlCandidate(
                    "c002",
                    "Site info",
                    "button",
                    (420, 180, 110, 32),
                    window_title="Dashboard - Google Chrome",
                ),
            ),
            (
                "Open site info in the app.",
                ControlCandidate(
                    "c001",
                    "site_info_lock",
                    "button",
                    (90, 8, 28, 34),
                ),
                ControlCandidate(
                    "c002",
                    "Site info",
                    "button",
                    (420, 180, 110, 32),
                    window_title="Dashboard - Google Chrome",
                ),
            ),
            (
                "Go forward in the wizard.",
                ControlCandidate(
                    "c001",
                    "Forward",
                    "button",
                    (52, 8, 34, 34),
                    automation_id="view_1002",
                    window_title="Onboarding - Google Chrome",
                ),
                ControlCandidate(
                    "c002",
                    "Forward",
                    "button",
                    (420, 540, 110, 32),
                    window_title="Onboarding - Google Chrome",
                ),
            ),
            (
                "Print report in the app.",
                ControlCandidate(
                    "c001",
                    "Print",
                    "button",
                    (760, 8, 42, 34),
                    window_title="Report - Google Chrome",
                ),
                ControlCandidate(
                    "c002",
                    "Print report",
                    "button",
                    (420, 180, 110, 32),
                    window_title="Report - Google Chrome",
                ),
            ),
        )
        for instruction, chrome_control, app_control in cases:
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": chrome_control.id,
                            "target": {
                                "x": chrome_control.rect[0],
                                "y": chrome_control.rect[1],
                                "width": chrome_control.rect[2],
                                "height": chrome_control.rect[3],
                            },
                        }
                    ),
                    self._capture(),
                    [chrome_control, app_control],
                )

                self.assertEqual(target.source, "text_match")
                self.assertEqual(target.target_id, app_control.id)
                self.assertFalse(target.rejected_reason)

    def test_downloads_folder_wording_rejects_browser_toolbar_downloads_button(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from help_session import resolve_help_target

        toolbar = ControlCandidate(
            "browser_downloads",
            "Downloads",
            "button",
            (900, 8, 42, 34),
            automation_id="downloads",
            window_title="Report - Google Chrome",
        )
        folder = ControlCandidate(
            "downloads_folder",
            "Downloads",
            "listitem",
            (100, 200, 200, 32),
            window_title="File Explorer",
        )
        candidates = [toolbar, folder]

        target_id = resolve_candidate_target(
            target_id="browser_downloads",
            instruction="Open downloads folder.",
            candidates=candidates,
            model_rect=toolbar.rect,
        )
        snap_target = snap_candidate_target(
            instruction="Open downloads folder.",
            candidates=candidates,
            model_rect=toolbar.rect,
        )
        help_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open downloads folder.",
                    "target_id": "browser_downloads",
                    "target": {
                        "x": toolbar.rect[0],
                        "y": toolbar.rect[1],
                        "width": toolbar.rect[2],
                        "height": toolbar.rect[3],
                    },
                }
            ),
            self._capture(),
            candidates,
        )

        self.assertEqual(target_id.target_id, "browser_downloads")
        self.assertEqual(target_id.rejected_reason, "target_id semantic mismatch")
        self.assertEqual(snap_target.target_id, "browser_downloads")
        self.assertEqual(snap_target.rejected_reason, "candidate semantic mismatch")
        self.assertEqual(help_target.target_id, "browser_downloads")
        self.assertEqual(help_target.rejected_reason, "target_id semantic mismatch")

    def test_taskbar_app_button_does_not_steal_in_app_target(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from help_session import resolve_help_target

        taskbar = ControlCandidate(
            "taskbar_report",
            "Report",
            "button",
            (80, 952, 96, 40),
            window_title="Taskbar",
            window_rank=1,
        )
        app_item = ControlCandidate(
            "app_reports",
            "Reports",
            "listitem",
            (120, 180, 180, 36),
            window_title="Dashboard",
            window_rank=0,
        )
        candidates = [taskbar, app_item]

        target_id = resolve_candidate_target(
            target_id="taskbar_report",
            instruction="Open Report in the app.",
            candidates=candidates,
            model_rect=taskbar.rect,
        )
        text_target = resolve_candidate_target(
            target_id="",
            instruction="Open Report in the app.",
            candidates=candidates,
            model_rect=taskbar.rect,
        )
        snap_target = snap_candidate_target(
            instruction="Open Report in the app.",
            candidates=candidates,
            model_rect=taskbar.rect,
        )
        help_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open Report in the app.",
                    "target_id": "taskbar_report",
                    "target": {
                        "x": taskbar.rect[0],
                        "y": taskbar.rect[1],
                        "width": taskbar.rect[2],
                        "height": taskbar.rect[3],
                    },
                }
            ),
            self._capture(),
            candidates,
        )

        self.assertEqual(target_id.target_id, "taskbar_report")
        self.assertEqual(target_id.rejected_reason, "target_id semantic mismatch")
        self.assertIsNone(text_target)
        self.assertIsNone(snap_target)
        self.assertEqual(help_target.target_id, "taskbar_report")
        self.assertEqual(help_target.rejected_reason, "target_id semantic mismatch")

    def test_os_chrome_controls_do_not_steal_app_local_targets(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            (
                "Minimize the panel in the app.",
                ControlCandidate(
                    "c001",
                    "Minimize",
                    "button",
                    (910, 0, 30, 30),
                    automation_id="Minimize",
                    window_title="Dashboard - Google Chrome",
                ),
                ControlCandidate(
                    "c002",
                    "Minimize panel",
                    "button",
                    (620, 140, 110, 32),
                    window_title="Dashboard - Google Chrome",
                ),
            ),
            (
                "Minimize the panel in the app.",
                ControlCandidate(
                    "c001",
                    "Minimize",
                    "button",
                    (910, 100, 30, 30),
                    automation_id="Minimize",
                    window_title="Dashboard - Google Chrome",
                ),
                ControlCandidate(
                    "c002",
                    "Minimize panel",
                    "button",
                    (620, 240, 110, 32),
                    window_title="Dashboard - Google Chrome",
                ),
            ),
            (
                "Search in the app.",
                ControlCandidate(
                    "c001",
                    "Search",
                    "button",
                    (80, 955, 160, 40),
                    automation_id="SearchGleamButton",
                    window_title="Taskbar",
                ),
                ControlCandidate(
                    "c002",
                    "Search",
                    "edit",
                    (300, 160, 240, 32),
                    window_title="Dashboard",
                ),
            ),
        )
        for instruction, os_control, app_control in cases:
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": os_control.id,
                            "target": {
                                "x": os_control.rect[0],
                                "y": os_control.rect[1],
                                "width": os_control.rect[2],
                                "height": os_control.rect[3],
                            },
                        }
                    ),
                    self._capture(),
                    [os_control, app_control],
                )

                self.assertEqual(target.source, "text_match")
                self.assertEqual(target.target_id, app_control.id)
                self.assertFalse(target.rejected_reason)

    def test_explicit_settings_tab_wording_still_accepts_browser_tab_title(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        target = resolve_candidate_target(
            target_id="tab",
            instruction="Open settings tab.",
            candidates=[
                ControlCandidate(
                    "tab",
                    "Settings - Google Chrome",
                    "tabitem",
                    (80, 0, 220, 40),
                    window_title="Settings - Google Chrome",
                )
            ],
            model_rect=(80, 0, 220, 40),
        )

        self.assertEqual(target.target_id, "tab")
        self.assertFalse(target.rejected_reason)

    def test_specific_settings_target_id_accepts_unnamed_url_bookmark(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        bookmark = (
            "Unnamed bookmark for "
            "https://platform.openai.com/settings/organization/billing/overview"
        )
        for instruction in (
            "Open OpenAI settings.",
            "Open billing settings.",
            "Open OpenAI organization settings.",
            "Open billing.",
        ):
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            bookmark,
                            "button",
                            (120, 160, 220, 32),
                            window_title="about:blank - Google Chrome",
                        )
                    ],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, (120, 160, 220, 32))

    def test_generic_settings_text_match_ignores_unnamed_url_bookmark(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open settings.",
                    "target": {"x": 120, "y": 160, "width": 220, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate(
                    "c001",
                    "Unnamed bookmark for "
                    "https://platform.openai.com/settings/organization/billing/overview",
                    "button",
                    (120, 160, 220, 32),
                    window_title="about:blank - Google Chrome",
                ),
                ControlCandidate("c002", "Settings", "button", (420, 160, 100, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c002")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (420, 160, 100, 32))

    def test_generic_settings_model_rect_does_not_snap_to_unnamed_url_bookmark(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open settings.",
                    "target": {"x": 120, "y": 160, "width": 220, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate(
                    "c001",
                    "Unnamed bookmark for "
                    "https://platform.openai.com/settings/organization/billing/overview",
                    "button",
                    (120, 160, 220, 32),
                    window_title="about:blank - Google Chrome",
                )
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.rejected_reason, "candidate snapshot no match")

    def test_generic_route_target_id_rejects_unnamed_url_bookmark(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            (
                "Open dashboard.",
                "Unnamed bookmark for "
                "https://dashboard.stripe.com/acct_1TQxqVCdMQikXj6B/balance/overview",
            ),
            (
                "Open overview.",
                "Unnamed bookmark for "
                "https://dashboard.stripe.com/acct_1TQxqVCdMQikXj6B/balance/overview",
            ),
            (
                "Open home.",
                "Unnamed bookmark for "
                "https://dash.cloudflare.com/5ae1354de89966fd627a61a76aa3e6dd/home/overview",
            ),
            (
                "Open account.",
                "Unnamed bookmark for "
                "https://www.name.com/account/domain/details/s2client.dev/dns",
            ),
            (
                "Open profile.",
                "Unnamed bookmark for "
                "https://www.name.com/account/domain/details/s2client.dev/dns",
            ),
            (
                "Open page.",
                "Unnamed bookmark for "
                "https://business.facebook.com/latest/?asset_id=1136461419546617"
                "&nav_ref=manage_page_ap_plus_left_nav_mbs_button",
            ),
            (
                "Open latest.",
                "Unnamed bookmark for "
                "https://business.facebook.com/latest/?asset_id=1136461419546617"
                "&nav_ref=manage_page_ap_plus_left_nav_mbs_button",
            ),
            (
                "Open asset.",
                "Unnamed bookmark for "
                "https://business.facebook.com/latest/?asset_id=1136461419546617"
                "&nav_ref=manage_page_ap_plus_left_nav_mbs_button",
            ),
            (
                "Open nav.",
                "Unnamed bookmark for "
                "https://business.facebook.com/latest/?asset_id=1136461419546617"
                "&nav_ref=manage_page_ap_plus_left_nav_mbs_button",
            ),
            (
                "Open ref.",
                "Unnamed bookmark for "
                "https://business.facebook.com/latest/?asset_id=1136461419546617"
                "&nav_ref=manage_page_ap_plus_left_nav_mbs_button",
            ),
            (
                "Open manage.",
                "Unnamed bookmark for "
                "https://business.facebook.com/latest/?asset_id=1136461419546617"
                "&nav_ref=manage_page_ap_plus_left_nav_mbs_button",
            ),
            (
                "Open 1136461419546617.",
                "Unnamed bookmark for "
                "https://business.facebook.com/latest/?asset_id=1136461419546617"
                "&nav_ref=manage_page_ap_plus_left_nav_mbs_button",
            ),
            (
                "Open platform.",
                "Unnamed bookmark for "
                "https://platform.openai.com/settings/organization/billing/overview",
            ),
            (
                "Open organization.",
                "Unnamed bookmark for "
                "https://platform.openai.com/settings/organization/billing/overview",
            ),
            (
                "Open folder.",
                "Unnamed bookmark for "
                "https://privateemail.com/appsuite/#!!&app=io.ox/mail&folder=default0/INBOX",
            ),
            (
                "Open cloud.",
                "Unnamed bookmark for "
                "https://console.cloud.google.com/apis/credentials?project=gen-lang-client-0559993646",
            ),
            (
                "Open credentials.",
                "Unnamed bookmark for "
                "https://console.cloud.google.com/apis/credentials?project=gen-lang-client-0559993646",
            ),
            (
                "Open project.",
                "Unnamed bookmark for "
                "https://console.cloud.google.com/apis/credentials?project=gen-lang-client-0559993646",
            ),
            (
                "Open client.",
                "Unnamed bookmark for "
                "https://console.cloud.google.com/apis/credentials?project=gen-lang-client-0559993646",
            ),
            (
                "Open org.",
                "Unnamed bookmark for "
                "https://supabase.com/dashboard/org/bowdgieoawwjypixwsbx",
            ),
            (
                "Open Claude platform.",
                "Unnamed bookmark for "
                "https://platform.openai.com/settings/organization/billing/overview",
            ),
            (
                "Open unnamed.",
                "Unnamed bookmark for https://github.com",
            ),
            (
                "Open unnamed bookmark.",
                "Unnamed bookmark for https://github.com",
            ),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            label,
                            "button",
                            (120, 160, 220, 32),
                            window_title="about:blank - Google Chrome",
                        )
                    ],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, "target_id semantic mismatch")

    def test_specific_route_target_id_requires_matching_unnamed_bookmark_destination(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        stripe = (
            "Unnamed bookmark for "
            "https://dashboard.stripe.com/acct_1TQxqVCdMQikXj6B/balance/overview"
        )
        supabase = (
            "Unnamed bookmark for "
            "https://supabase.com/dashboard/org/bowdgieoawwjypixwsbx"
        )
        cloudflare = (
            "Unnamed bookmark for "
            "https://dash.cloudflare.com/5ae1354de89966fd627a61a76aa3e6dd/home/overview"
        )
        google_cloud = (
            "Unnamed bookmark for "
            "https://console.cloud.google.com/apis/credentials?project=gen-lang-client-0559993646"
        )
        facebook_page = (
            "Unnamed bookmark for "
            "https://business.facebook.com/latest/?asset_id=1136461419546617"
            "&nav_ref=manage_page_ap_plus_left_nav_mbs_button"
        )
        claude_platform = (
            "Unnamed bookmark for https://platform.claude.com/workspaces/default/cost"
        )
        openai_platform = (
            "Unnamed bookmark for "
            "https://platform.openai.com/settings/organization/billing/overview"
        )
        cases = (
            ("Open Stripe dashboard.", stripe, ""),
            ("Open Supabase dashboard.", supabase, ""),
            ("Open Supabase org.", supabase, ""),
            ("Open Cloudflare overview.", cloudflare, ""),
            ("Open Google Cloud.", google_cloud, ""),
            ("Open Google Cloud credentials.", google_cloud, ""),
            ("Open Facebook page.", facebook_page, ""),
            ("Open business Facebook.", facebook_page, ""),
            ("Open Claude platform.", claude_platform, ""),
            ("Open Stripe dashboard.", supabase, "target_id semantic mismatch"),
            ("Open Supabase dashboard.", stripe, "target_id semantic mismatch"),
            ("Open Cloudflare overview.", stripe, "target_id semantic mismatch"),
            ("Open Google page.", facebook_page, "target_id semantic mismatch"),
            ("Open Claude platform.", openai_platform, "target_id semantic mismatch"),
            ("Open Claude platform.", stripe, "target_id semantic mismatch"),
        )
        for instruction, label, reason in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            label,
                            "button",
                            (120, 160, 220, 32),
                            window_title="about:blank - Google Chrome",
                        )
                    ],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, reason)

    def test_generic_bookmark_action_rejects_unnamed_url_bookmark(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            "Open bookmark.",
            "Open favorite.",
            "Open star.",
            "Bookmark this.",
            "Favorite this item.",
            "Click bookmark.",
        )
        for instruction in cases:
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            "Unnamed bookmark for https://github.com",
                            "button",
                            (120, 160, 220, 32),
                            window_title="about:blank - Google Chrome",
                        )
                    ],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, "target_id semantic mismatch")

    def test_specific_bookmark_action_requires_matching_unnamed_bookmark_destination(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        github = "Unnamed bookmark for https://github.com"
        stripe = (
            "Unnamed bookmark for "
            "https://dashboard.stripe.com/acct_1TQxqVCdMQikXj6B/balance/overview"
        )
        openai = (
            "Unnamed bookmark for "
            "https://platform.openai.com/settings/organization/billing/overview"
        )
        cases = (
            ("Open GitHub bookmark.", github, ""),
            ("Open GitHub.", github, ""),
            ("Open Stripe bookmark.", stripe, ""),
            ("Open OpenAI billing bookmark.", openai, ""),
            ("Open GitHub bookmark.", stripe, "target_id semantic mismatch"),
            ("Open Stripe bookmark.", github, "target_id semantic mismatch"),
            ("Open OpenAI billing bookmark.", stripe, "target_id semantic mismatch"),
        )
        for instruction, label, reason in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            label,
                            "button",
                            (120, 160, 220, 32),
                            window_title="about:blank - Google Chrome",
                        )
                    ],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, reason)

    def test_generic_bookmark_text_match_prefers_star_over_unnamed_bookmark(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Bookmark this item.",
                    "target": {"x": 120, "y": 160, "width": 220, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate(
                    "c001",
                    "Unnamed bookmark for https://github.com",
                    "button",
                    (120, 160, 220, 32),
                    window_title="about:blank - Google Chrome",
                ),
                ControlCandidate("c002", "Star", "button", (360, 160, 80, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c002")
        self.assertFalse(target.rejected_reason)

    def test_add_bookmark_prefers_bookmark_button_over_new_tab(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate(
                "c001",
                "New Tab",
                "button",
                (120, 160, 32, 32),
                window_title="about:blank - Google Chrome",
            ),
            ControlCandidate(
                "c002",
                "Bookmark this tab",
                "button",
                (180, 160, 32, 32),
                window_title="about:blank - Google Chrome",
            ),
        ]

        text_target = resolve_candidate_target(
            target_id="",
            instruction="Add bookmark.",
            candidates=candidates,
            model_rect=None,
        )
        self.assertIsNotNone(text_target)
        assert text_target is not None
        self.assertEqual(text_target.source, "text_match")
        self.assertEqual(text_target.target_id, "c002")
        self.assertFalse(text_target.rejected_reason)

        cases = (
            {
                "kind": "step",
                "instruction": "Add bookmark.",
                "target_id": "c001",
            },
            {
                "kind": "step",
                "instruction": "Add bookmark.",
                "target": {"x": 120, "y": 160, "width": 32, "height": 32},
            },
            {
                "kind": "step",
                "instruction": "Add bookmark.",
                "target": {"x": 180, "y": 160, "width": 32, "height": 32},
            },
            {
                "kind": "step",
                "instruction": "Bookmark this tab.",
                "target_id": "c002",
            },
        )
        for payload in cases:
            with self.subTest(payload=payload):
                target = resolve_help_target(
                    self._decision(payload),
                    self._capture(),
                    candidates,
                )

                self.assertIn(target.source, {"target_id", "text_match"})
                self.assertEqual(target.target_id, "c002")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, (180, 160, 32, 32))

    def test_generic_bookmark_model_rect_rejects_unnamed_bookmark_snap(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Bookmark this item.",
                    "target": {"x": 120, "y": 160, "width": 220, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate(
                    "c001",
                    "Unnamed bookmark for https://github.com",
                    "button",
                    (120, 160, 220, 32),
                    window_title="about:blank - Google Chrome",
                )
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.rejected_reason, "candidate snapshot no match")

    def test_generic_url_path_words_reject_unnamed_url_bookmark(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        claude_new = "Unnamed bookmark for https://claude.ai/new"
        gemini_app = (
            "Unnamed bookmark for "
            "https://gemini.google.com/app?utm_source=app_launcher&utm_medium=owned"
            "&utm_campaign=base_all"
        )
        cases = (
            ("Open new.", claude_new),
            ("Create new.", claude_new),
            ("Add new.", claude_new),
            ("Open app.", gemini_app),
            ("Open launcher.", gemini_app),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            label,
                            "button",
                            (120, 160, 220, 32),
                            window_title="about:blank - Google Chrome",
                        )
                    ],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, "target_id semantic mismatch")

    def test_url_path_words_accept_only_matching_unnamed_bookmark_destination(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        claude_new = "Unnamed bookmark for https://claude.ai/new"
        gemini_app = (
            "Unnamed bookmark for "
            "https://gemini.google.com/app?utm_source=app_launcher&utm_medium=owned"
            "&utm_campaign=base_all"
        )
        cases = (
            ("Open Claude new.", claude_new, ""),
            ("Open Claude.", claude_new, ""),
            ("Open Gemini app.", gemini_app, ""),
            ("Open Gemini.", gemini_app, ""),
            ("Open Chrome app.", gemini_app, "target_id semantic mismatch"),
            ("Open Gemini app.", claude_new, "target_id semantic mismatch"),
        )
        for instruction, label, reason in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            label,
                            "button",
                            (120, 160, 220, 32),
                            window_title="about:blank - Google Chrome",
                        )
                    ],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, reason)

    def test_generic_new_rejects_new_tab_and_unnamed_bookmark(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open new.",
                    "target": {"x": 120, "y": 160, "width": 220, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate(
                    "c001",
                    "Unnamed bookmark for https://claude.ai/new",
                    "button",
                    (120, 160, 220, 32),
                    window_title="about:blank - Google Chrome",
                ),
                ControlCandidate(
                    "c002",
                    "New Tab",
                    "button",
                    (400, 160, 100, 32),
                    window_title="about:blank - Google Chrome",
                ),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.rejected_reason, "candidate snapshot no match")

    def test_generic_app_model_rect_rejects_unnamed_bookmark_snap(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open app.",
                    "target": {"x": 120, "y": 160, "width": 220, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate(
                    "c001",
                    "Unnamed bookmark for "
                    "https://gemini.google.com/app?utm_source=app_launcher",
                    "button",
                    (120, 160, 220, 32),
                    window_title="about:blank - Google Chrome",
                )
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.rejected_reason, "candidate snapshot no match")

    def test_generic_route_text_match_ignores_unnamed_url_bookmark(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open dashboard.",
                    "target": {"x": 120, "y": 160, "width": 220, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate(
                    "c001",
                    "Unnamed bookmark for "
                    "https://dashboard.stripe.com/acct_1TQxqVCdMQikXj6B/balance/overview",
                    "button",
                    (120, 160, 220, 32),
                    window_title="about:blank - Google Chrome",
                ),
                ControlCandidate(
                    "c002",
                    "GitHub Dashboard",
                    "tabitem",
                    (420, 160, 180, 32),
                    window_title="GitHub Dashboard - Google Chrome",
                ),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c002")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (420, 160, 180, 32))

    def test_generic_route_text_match_ignores_unnamed_url_bookmark_terms(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        cases = (
            (
                "Open page.",
                "Unnamed bookmark for "
                "https://business.facebook.com/latest/?asset_id=1136461419546617"
                "&nav_ref=manage_page_ap_plus_left_nav_mbs_button",
            ),
            (
                "Open project.",
                "Unnamed bookmark for "
                "https://console.cloud.google.com/apis/credentials"
                "?project=gen-lang-client-0559993646",
            ),
            (
                "Open organization.",
                "Unnamed bookmark for "
                "https://platform.openai.com/settings/organization/billing/overview",
            ),
            (
                "Open folder.",
                "Unnamed bookmark for "
                "https://privateemail.com/appsuite/#!!&app=io.ox/mail&folder=default0/INBOX",
            ),
            (
                "Open unnamed.",
                "Unnamed bookmark for https://github.com",
            ),
            (
                "Open unnamed bookmark.",
                "Unnamed bookmark for https://github.com",
            ),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                result = resolve_candidate_target(
                    target_id="",
                    instruction=instruction,
                    candidates=[
                        ControlCandidate(
                            "c001",
                            label,
                            "button",
                            (120, 160, 220, 32),
                            window_title="about:blank - Google Chrome",
                        )
                    ],
                )

                self.assertIsNone(result)

    def test_generic_route_model_rect_rejects_unnamed_bookmark_snap(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            (
                "Open page.",
                "Unnamed bookmark for "
                "https://business.facebook.com/latest/?asset_id=1136461419546617"
                "&nav_ref=manage_page_ap_plus_left_nav_mbs_button",
            ),
            (
                "Open project.",
                "Unnamed bookmark for "
                "https://console.cloud.google.com/apis/credentials"
                "?project=gen-lang-client-0559993646",
            ),
            (
                "Open organization.",
                "Unnamed bookmark for "
                "https://platform.openai.com/settings/organization/billing/overview",
            ),
            (
                "Open folder.",
                "Unnamed bookmark for "
                "https://privateemail.com/appsuite/#!!&app=io.ox/mail&folder=default0/INBOX",
            ),
            (
                "Open unnamed.",
                "Unnamed bookmark for https://github.com",
            ),
            (
                "Open unnamed bookmark.",
                "Unnamed bookmark for https://github.com",
            ),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target": {"x": 120, "y": 160, "width": 220, "height": 32},
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            label,
                            "button",
                            (120, 160, 220, 32),
                            window_title="about:blank - Google Chrome",
                        )
                    ],
                )

                self.assertEqual(target.source, "candidate_snap")
                self.assertEqual(target.rejected_reason, "candidate snapshot no match")

    def test_extension_access_target_id_requires_named_extension(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        claude_access = "Open Claude\nWants access to this site"
        codex_access = "Codex\nHas access to this site"
        cases = (
            ("Open site.", claude_access, "target_id semantic mismatch"),
            ("Open this site.", claude_access, "target_id semantic mismatch"),
            ("Open access.", claude_access, "target_id semantic mismatch"),
            ("Open site access.", claude_access, "target_id semantic mismatch"),
            ("Open has.", codex_access, "target_id semantic mismatch"),
            ("Click has.", codex_access, "target_id semantic mismatch"),
            ("Open wants.", claude_access, "target_id semantic mismatch"),
            ("Grant access.", claude_access, "target_id semantic mismatch"),
            ("Grant Claude access.", claude_access, ""),
            ("Allow Claude on this site.", claude_access, ""),
            ("Open Claude.", claude_access, ""),
            ("Grant Claude access.", codex_access, "target_id semantic mismatch"),
            ("Open Codex.", codex_access, ""),
        )
        for instruction, label, reason in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            label,
                            "button",
                            (120, 160, 180, 32),
                            window_title="about:blank - Google Chrome",
                        )
                    ],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, reason)

    def test_extension_status_words_ignore_access_button_text_match(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        for instruction in ("Open has.", "Click has."):
            with self.subTest(instruction=instruction):
                result = resolve_candidate_target(
                    target_id="",
                    instruction=instruction,
                    candidates=[
                        ControlCandidate(
                            "c001",
                            "Codex\nHas access to this site",
                            "button",
                            (120, 160, 180, 32),
                            window_title="GitHub - Google Chrome",
                        )
                    ],
                )

                self.assertIsNone(result)

    def test_extension_status_words_reject_access_button_snap(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        for instruction in ("Open has.", "Click has.", "Click access button."):
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target": {"x": 120, "y": 160, "width": 180, "height": 32},
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            "Codex\nHas access to this site",
                            "button",
                            (120, 160, 180, 32),
                            window_title="GitHub - Google Chrome",
                        )
                    ],
                )

                self.assertEqual(target.source, "candidate_snap")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, "candidate semantic mismatch")

    def test_site_information_target_id_requires_info_or_lock_wording(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Open site.", "target_id semantic mismatch"),
            ("Open this site.", "target_id semantic mismatch"),
            ("Open site access.", "target_id semantic mismatch"),
            ("Open view.", "target_id semantic mismatch"),
            ("Click view.", "target_id semantic mismatch"),
            ("Open site information.", ""),
            ("Click the site info button.", ""),
            ("Click the lock icon.", ""),
            ("Click the padlock icon.", ""),
            ("View site information.", ""),
        )
        for instruction, reason in cases:
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            "View site information",
                            "button",
                            (120, 160, 160, 32),
                            window_title="about:blank - Google Chrome",
                        )
                    ],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, reason)

    def test_site_information_text_match_ignores_generic_view(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="",
            instruction="Open view.",
            candidates=[
                ControlCandidate(
                    "c001",
                    "View site information",
                    "button",
                    (120, 160, 160, 32),
                    automation_id="view_1011",
                    window_title="GitHub Dashboard - Google Chrome",
                )
            ],
        )

        self.assertIsNone(result)

    def test_site_information_model_rect_rejects_generic_view_snap(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open view.",
                    "target": {"x": 120, "y": 160, "width": 160, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate(
                    "c001",
                    "View site information",
                    "button",
                    (120, 160, 160, 32),
                    automation_id="view_1011",
                    window_title="GitHub Dashboard - Google Chrome",
                )
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.target_id, "c001")
        self.assertEqual(target.rejected_reason, "candidate semantic mismatch")

    def test_site_information_text_match_recovers_from_extension_access_target_id(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open site information.",
                    "target_id": "c001",
                }
            ),
            self._capture(),
            [
                ControlCandidate(
                    "c001",
                    "Open Claude\nWants access to this site",
                    "button",
                    (120, 160, 180, 32),
                    window_title="about:blank - Google Chrome",
                ),
                ControlCandidate(
                    "c002",
                    "View site information",
                    "button",
                    (420, 160, 160, 32),
                    window_title="about:blank - Google Chrome",
                ),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c002")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (420, 160, 160, 32))

    def test_info_target_id_accepts_common_labels_and_icons(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Show info.", "Information", (120, 160, 120, 32)),
            ("Open information.", "\u2139", (120, 160, 32, 32)),
            ("Show details.", "\U0001f6c8", (120, 160, 32, 32)),
            ("Open about.", "\u24d8", (120, 160, 32, 32)),
        )
        for instruction, label, rect in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [ControlCandidate("c001", label, "button", rect)],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, rect)

    def test_info_icon_text_match_overrides_help_geometry(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Show info.",
                    "target": {"x": 300, "y": 160, "width": 80, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "\u2139", "button", (120, 160, 32, 32)),
                ControlCandidate("c002", "Help", "button", (300, 160, 80, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (120, 160, 32, 32))

    def test_info_aliases_do_not_cross_help_controls(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Show info.", "?"),
            ("Open help.", "\u2139"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [ControlCandidate("c001", label, "button", (120, 160, 32, 32))],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, "target_id semantic mismatch")

    def test_pin_target_id_accepts_pushpin_labels_and_icons(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Pin this item.", "Pushpin", (120, 160, 100, 32)),
            ("Pin this chat.", "Thumbtack", (120, 160, 120, 32)),
            ("Pin to top.", "Pinned", (120, 160, 100, 32)),
            ("Click the pushpin.", "Pin", (120, 160, 80, 32)),
            ("Click the thumbtack.", "Pin", (120, 160, 80, 32)),
            ("Unpin this item.", "Pushpin", (120, 160, 100, 32)),
            ("Pin this item.", "\U0001f4cc", (120, 160, 32, 32)),
            ("Pin this item.", "\U0001f588", (120, 160, 32, 32)),
        )
        for instruction, label, rect in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [ControlCandidate("c001", label, "button", rect)],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, rect)

    def test_pin_state_wording_rejects_opposite_pin_button(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target
        from help_session import resolve_help_target

        cases = (
            ("Unpin this item.", "Pin"),
            ("Pin this item.", "Unpin"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                candidates = [
                    ControlCandidate("c001", label, "button", (120, 160, 100, 32))
                ]
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    candidates,
                )
                text_target = resolve_candidate_target(
                    target_id="",
                    instruction=instruction,
                    candidates=candidates,
                )
                snap_target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target": {"x": 120, "y": 160, "width": 100, "height": 32},
                        }
                    ),
                    self._capture(),
                    candidates,
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, "target_id semantic mismatch")
                self.assertIsNone(text_target)
                self.assertEqual(snap_target.source, "candidate_snap")
                self.assertEqual(snap_target.target_id, "c001")
                self.assertEqual(snap_target.rejected_reason, "candidate semantic mismatch")

    def test_pin_icon_text_match_overrides_archive_geometry(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Pin this item.",
                    "target": {"x": 300, "y": 160, "width": 100, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "\U0001f4cc", "button", (120, 160, 32, 32)),
                ControlCandidate("c002", "Archive", "button", (300, 160, 100, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (120, 160, 32, 32))

    def test_pin_alias_rejects_ambiguous_pin_and_pushpin_actions(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Pin this item.",
                    "target_id": "c001",
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Pushpin", "button", (120, 160, 100, 32)),
                ControlCandidate("c002", "Pin", "button", (280, 160, 80, 32)),
            ],
        )

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.target_id, "c001")
        self.assertEqual(target.rejected_reason, "target_id ambiguous")

    def test_same_action_object_mismatch_recovers_to_exact_label(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from help_session import resolve_help_target

        cases = (
            ("Pin Alpha.", "Pin"),
            ("Unpin Alpha.", "Unpin"),
            ("Restore Alpha.", "Restore"),
            ("Hide Alpha.", "Hide"),
            ("Show Alpha.", "Show"),
            ("Search Alpha.", "Search"),
            ("Find Alpha.", "Find"),
            ("Clear Alpha.", "Clear"),
            ("Reset Alpha.", "Reset"),
        )
        for instruction, action in cases:
            with self.subTest(instruction=instruction):
                wrong = ControlCandidate("wrong", f"{action} Beta", "button", (120, 160, 120, 32))
                correct = ControlCandidate("correct", f"{action} Alpha", "button", (300, 160, 120, 32))
                candidates = [wrong, correct]

                wrong_target = resolve_candidate_target(
                    target_id="wrong",
                    instruction=instruction,
                    candidates=candidates,
                    model_rect=wrong.rect,
                )
                snap_target = snap_candidate_target(
                    instruction=instruction,
                    candidates=candidates,
                    model_rect=wrong.rect,
                )
                help_target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "wrong",
                            "target": {
                                "x": wrong.rect[0],
                                "y": wrong.rect[1],
                                "width": wrong.rect[2],
                                "height": wrong.rect[3],
                            },
                        }
                    ),
                    self._capture(),
                    candidates,
                )

                self.assertEqual(wrong_target.target_id, "wrong")
                self.assertEqual(wrong_target.rejected_reason, "target_id semantic mismatch")
                self.assertEqual(snap_target.target_id, "wrong")
                self.assertEqual(snap_target.rejected_reason, "candidate semantic mismatch")
                self.assertEqual(help_target.source, "text_match")
                self.assertEqual(help_target.target_id, "correct")
                self.assertFalse(help_target.rejected_reason)
                self.assertEqual(help_target.rect, correct.rect)

    def test_pin_action_rejects_taskbar_pinned_app_state_labels(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            "Pin this item.",
            "Pin Google Chrome.",
        )
        for instruction in cases:
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            "Google Chrome pinned",
                            "button",
                            (120, 160, 180, 32),
                            window_title="Taskbar",
                        ),
                    ],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, "target_id semantic mismatch")

    def test_generic_taskbar_app_state_words_reject_state_labels(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Open running.", "Google Chrome - 5 running windows"),
            ("Open running windows.", "Google Chrome - 5 running windows"),
            ("Open windows.", "Google Chrome - 5 running windows"),
            ("Open 5 windows.", "Google Chrome - 5 running windows"),
            ("Open running app.", "Cursor - 1 running window"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            label,
                            "button",
                            (120, 160, 220, 32),
                            window_title="Taskbar",
                        ),
                    ],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, "target_id semantic mismatch")

    def test_named_taskbar_app_state_label_still_matches_app_instruction(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Open Google Chrome.", "Google Chrome - 5 running windows"),
            ("Open Claude.", "Claude - 1 running window pinned"),
            ("Open Cursor.", "Cursor - 1 running window"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            label,
                            "button",
                            (120, 160, 220, 32),
                            window_title="Taskbar",
                        ),
                    ],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)

    def test_generic_taskbar_status_words_reject_onedrive_label(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            "Open one.",
            "Open drive.",
            "Open personal.",
            "Open synced.",
            "Open backed up.",
        )
        for instruction in cases:
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            "OneDrive - Personal\r\nBacked up and synced",
                            "button",
                            (120, 160, 220, 32),
                            window_title="Taskbar",
                        ),
                    ],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, "target_id semantic mismatch")

    def test_named_onedrive_status_label_still_matches_service_instruction(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            "Open OneDrive.",
            "Open OneDrive personal.",
        )
        for instruction in cases:
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            "OneDrive - Personal\r\nBacked up and synced",
                            "button",
                            (120, 160, 220, 32),
                            window_title="Taskbar",
                        ),
                    ],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)

    def test_generic_taskbar_state_and_status_reject_model_rect_snap(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Open running windows.", "Google Chrome - 5 running windows"),
            ("Open backed up.", "OneDrive - Personal\r\nBacked up and synced"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target": {"x": 120, "y": 160, "width": 220, "height": 32},
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            label,
                            "button",
                            (120, 160, 220, 32),
                            window_title="Taskbar",
                        ),
                    ],
                )

                self.assertEqual(target.source, "candidate_snap")
                self.assertEqual(target.rejected_reason, "candidate snapshot no match")

    def test_generic_taskbar_tray_status_words_reject_dynamic_labels(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Open 64.", "Widgets 64\u00b0F Clear", ""),
            ("Open access.", "Network StarLink\nInternet access", ""),
            ("Open 24%.", "Volume Speakers (Realtek(R) Audio): 24%", ""),
            (
                "Open status.",
                "Power Battery status: 80% remaining\r\nFully smart charged",
                "",
            ),
            (
                "Open remaining.",
                "Power Battery status: 80% remaining\r\nFully smart charged",
                "",
            ),
            (
                "Open smart charged.",
                "Power Battery status: 80% remaining\r\nFully smart charged",
                "",
            ),
            ("Open AM.", "Clock 5:04 AM\n\u200e6/\u200e1/\u200e2026", ""),
            ("Open 2026.", "Clock 5:04 AM\n\u200e6/\u200e1/\u200e2026", ""),
            ("Open reef.", "Search - World Reef Awareness Day", "SearchGleamButton"),
            (
                "Open awareness day.",
                "Search - World Reef Awareness Day",
                "SearchGleamButton",
            ),
        )
        for instruction, label, automation_id in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            label,
                            "button",
                            (120, 160, 220, 32),
                            automation_id=automation_id,
                            window_title="Taskbar",
                        ),
                    ],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, "target_id semantic mismatch")

    def test_localized_taskbar_search_status_separator_does_not_match_minus_alias(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target

        candidate = ControlCandidate(
            "c001",
            "搜索 - 世界珊瑚礁日",
            "button",
            (120, 160, 220, 32),
            automation_id="SearchGleamButton",
            window_title="Taskbar",
        )
        for instruction in ("Zoom out.", "Minimize.", "Click minus."):
            with self.subTest(instruction=instruction):
                text_target = resolve_candidate_target(
                    target_id="",
                    instruction=instruction,
                    candidates=[candidate],
                )
                target_id = resolve_candidate_target(
                    target_id="c001",
                    instruction=instruction,
                    candidates=[candidate],
                )
                snap_target = snap_candidate_target(
                    instruction=instruction,
                    candidates=[candidate],
                    model_rect=candidate.rect,
                )

                self.assertIsNone(text_target)
                self.assertEqual(target_id.source, "target_id")
                self.assertEqual(target_id.rejected_reason, "target_id semantic mismatch")
                self.assertEqual(snap_target.source, "candidate_snap")
                self.assertEqual(snap_target.rejected_reason, "candidate semantic mismatch")

    def test_localized_label_separator_does_not_match_minus_alias(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target

        candidate = ControlCandidate(
            "c001",
            "搜索 - 世界珊瑚礁日",
            "button",
            (120, 160, 220, 32),
            window_title="Browser",
        )
        for instruction in ("Zoom out.", "Minimize.", "Click minus."):
            with self.subTest(instruction=instruction):
                text_target = resolve_candidate_target(
                    target_id="",
                    instruction=instruction,
                    candidates=[candidate],
                )
                target_id = resolve_candidate_target(
                    target_id="c001",
                    instruction=instruction,
                    candidates=[candidate],
                )
                snap_target = snap_candidate_target(
                    instruction=instruction,
                    candidates=[candidate],
                    model_rect=candidate.rect,
                )

                self.assertIsNone(text_target)
                self.assertEqual(target_id.source, "target_id")
                self.assertEqual(target_id.rejected_reason, "target_id semantic mismatch")
                self.assertEqual(snap_target.source, "candidate_snap")
                self.assertEqual(snap_target.rejected_reason, "candidate semantic mismatch")

    def test_named_taskbar_tray_status_labels_still_match_stable_identity(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Open weather.", "Widgets 64\u00b0F Clear", ""),
            ("Open widgets.", "Widgets 64\u00b0F Clear", ""),
            ("Open internet access.", "Network StarLink\nInternet access", ""),
            ("Open network.", "Network StarLink\nInternet access", ""),
            ("Open volume.", "Volume Speakers (Realtek(R) Audio): 24%", ""),
            ("Open speakers.", "Volume Speakers (Realtek(R) Audio): 24%", ""),
            ("Open Realtek.", "Volume Speakers (Realtek(R) Audio): 24%", ""),
            (
                "Open battery.",
                "Power Battery status: 80% remaining\r\nFully smart charged",
                "",
            ),
            (
                "Open power.",
                "Power Battery status: 80% remaining\r\nFully smart charged",
                "",
            ),
            ("Open clock.", "Clock 5:04 AM\n\u200e6/\u200e1/\u200e2026", ""),
            ("Open time.", "Clock 5:04 AM\n\u200e6/\u200e1/\u200e2026", ""),
            ("Open search.", "Search - World Reef Awareness Day", "SearchGleamButton"),
        )
        for instruction, label, automation_id in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            label,
                            "button",
                            (120, 160, 220, 32),
                            automation_id=automation_id,
                            window_title="Taskbar",
                        ),
                    ],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)

    def test_numeric_taskbar_clock_status_accepts_clock_request(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate(
                "clock",
                "11:32 AM\n6/1/2026",
                "button",
                (900, 960, 90, 40),
                automation_id="SystemTrayIcon",
                window_title="Taskbar",
            )
        ]
        instruction = "Open clock."

        target_id = resolve_candidate_target(
            target_id="clock",
            instruction=instruction,
            candidates=candidates,
            model_rect=(900, 960, 90, 40),
        )
        text_target = resolve_candidate_target(
            target_id="",
            instruction=instruction,
            candidates=candidates,
            model_rect=(900, 960, 90, 40),
        )
        snap_target = snap_candidate_target(
            instruction=instruction,
            candidates=candidates,
            model_rect=(900, 960, 90, 40),
        )
        help_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": instruction,
                    "target_id": "clock",
                    "target": {"x": 900, "y": 960, "width": 90, "height": 40},
                }
            ),
            self._capture(),
            candidates,
        )

        for resolved in (target_id, text_target, snap_target, help_target):
            self.assertEqual(resolved.target_id, "clock")
            self.assertFalse(resolved.rejected_reason)
            self.assertEqual(resolved.rect, (900, 960, 90, 40))

    def test_generic_taskbar_tray_status_words_reject_model_rect_snap(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Open 24%.", "Volume Speakers (Realtek(R) Audio): 24%", ""),
            ("Open reef.", "Search - World Reef Awareness Day", "SearchGleamButton"),
        )
        for instruction, label, automation_id in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target": {"x": 120, "y": 160, "width": 220, "height": 32},
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            label,
                            "button",
                            (120, 160, 220, 32),
                            automation_id=automation_id,
                            window_title="Taskbar",
                        ),
                    ],
                )

                self.assertEqual(target.source, "candidate_snap")
                self.assertEqual(target.rejected_reason, "candidate snapshot no match")

    def test_browser_named_group_rejects_generic_state_words(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            "Open closed.",
            "Open group.",
            "Open closed group.",
        )
        for instruction in cases:
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            "Limitless group - Closed",
                            "button",
                            (120, 160, 180, 32),
                            window_title="about:blank - Google Chrome",
                        ),
                    ],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, "target_id semantic mismatch")

    def test_browser_named_group_requires_matching_name(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Open Limitless group.", "AgenticField group - Closed"),
            ("Open AgenticField group.", "Limitless group - Closed"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            label,
                            "button",
                            (120, 160, 180, 32),
                            window_title="about:blank - Google Chrome",
                        ),
                    ],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, "target_id semantic mismatch")

    def test_browser_named_group_accepts_matching_name_and_tab_groups_button(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Open tab groups.", "Tab groups"),
            ("Open AgenticField.", "AgenticField group - Closed"),
            ("Open AgenticField group.", "AgenticField group - Closed"),
            ("Open Limitless group.", "Limitless group - Closed"),
            ("Open B2B.", "B2B group - Closed"),
            ("Open Collage.", "Collage group - Closed"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            label,
                            "button",
                            (120, 160, 180, 32),
                            window_title="about:blank - Google Chrome",
                        ),
                    ],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)

    def test_browser_named_group_rejects_generic_state_model_rect_snap(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open closed group.",
                    "target": {"x": 120, "y": 160, "width": 180, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate(
                    "c001",
                    "Limitless group - Closed",
                    "button",
                    (120, 160, 180, 32),
                    window_title="about:blank - Google Chrome",
                ),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.rejected_reason, "candidate snapshot no match")

    def test_bold_action_rejects_b2b_browser_group(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            "Open bold.",
            "Click bold.",
            "Make it bold.",
        )
        for instruction in cases:
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            "B2B group - Closed",
                            "button",
                            (120, 160, 180, 32),
                            window_title="about:blank - Google Chrome",
                        ),
                    ],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, "target_id semantic mismatch")

    def test_bold_action_recovers_from_b2b_group_to_bold_button(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open bold.",
                    "target_id": "c001",
                }
            ),
            self._capture(),
            [
                ControlCandidate(
                    "c001",
                    "B2B group - Closed",
                    "button",
                    (120, 160, 180, 32),
                    window_title="about:blank - Google Chrome",
                ),
                ControlCandidate("c002", "Bold", "button", (400, 160, 80, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c002")
        self.assertFalse(target.rejected_reason)

    def test_mail_target_id_accepts_envelope_labels_and_icons(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Open email.", "Envelope", (120, 160, 100, 32)),
            ("Open mail.", "\u2709", (120, 160, 32, 32)),
            ("Open email.", "\U0001f4e7", (120, 160, 32, 32)),
            ("Open mail.", "\U0001f4e8", (120, 160, 32, 32)),
            ("Open email.", "\U0001f4e9", (120, 160, 32, 32)),
            (
                "Open mail.",
                "Recibidos (3.921) - abelvalencianacarreon@gmail.com - Gmail - Memory usage - 270 MB",
                (120, 160, 260, 32),
            ),
            (
                "Open email.",
                "Recibidos (3.921) - abelvalencianacarreon@gmail.com - Gmail - Memory usage - 270 MB",
                (120, 160, 260, 32),
            ),
            (
                "Open inbox.",
                "Recibidos (3.921) - abelvalencianacarreon@gmail.com - Gmail - Memory usage - 270 MB",
                (120, 160, 260, 32),
            ),
        )
        for instruction, label, rect in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [ControlCandidate("c001", label, "button", rect)],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, rect)

    def test_gmail_control_aliases_do_not_cross_generic_mail_or_email_fields(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        gmail_tab = (
            "Recibidos (3.921) - abelvalencianacarreon@gmail.com - Gmail - "
            "Memory usage - 270 MB"
        )
        cloudflare_email_tab = (
            "DNS | Records | limitles.dev | Abelnavarrocarreon@gmail.com's Account | "
            "Cloudflare - Memory usage - 580 MB"
        )
        cases = (
            ("Open Gmail.", "Mail", "button", "target_id semantic mismatch"),
            ("Open Gmail.", cloudflare_email_tab, "tabitem", "target_id semantic mismatch"),
            ("Open mail.", cloudflare_email_tab, "tabitem", "target_id semantic mismatch"),
            ("Open email.", cloudflare_email_tab, "tabitem", "target_id semantic mismatch"),
            ("Type your email.", gmail_tab, "tabitem", "target_id control type mismatch"),
        )
        for instruction, label, control_type, reason in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001", label, control_type, (120, 160, 260, 32)
                        )
                    ],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, reason)

    def test_gmail_text_match_recovers_from_email_address_tab_to_gmail_tab(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open Gmail.",
                    "target": {"x": 120, "y": 160, "width": 184, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate(
                    "c001",
                    "DNS | Records | limitles.dev | "
                    "Abelnavarrocarreon@gmail.com's Account | Cloudflare - "
                    "Memory usage - 580 MB",
                    "tabitem",
                    (120, 160, 184, 32),
                    window_title="GitHub Dashboard - Google Chrome",
                ),
                ControlCandidate(
                    "c002",
                    "Recibidos (3.921) - abelvalencianacarreon@gmail.com - Gmail - "
                    "Memory usage - 270 MB",
                    "tabitem",
                    (360, 160, 184, 32),
                    window_title="GitHub Dashboard - Google Chrome",
                ),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c002")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (360, 160, 184, 32))

    def test_gmail_model_rect_rejects_email_address_tab_snap(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open Gmail.",
                    "target": {"x": 120, "y": 160, "width": 184, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate(
                    "c001",
                    "DNS | Records | limitles.dev | "
                    "Abelnavarrocarreon@gmail.com's Account | Cloudflare - "
                    "Memory usage - 580 MB",
                    "tabitem",
                    (120, 160, 184, 32),
                    window_title="GitHub Dashboard - Google Chrome",
                )
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.target_id, "c001")
        self.assertEqual(target.rejected_reason, "candidate semantic mismatch")

    def test_chrome_tab_memory_usage_suffix_is_not_title_evidence(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            (
                "Open memory.",
                "Home - Limitless - Stripe - Memory usage - 687 MB",
                "target_id semantic mismatch",
            ),
            (
                "Open usage.",
                "Home - Limitless - Stripe - Memory usage - 687 MB",
                "target_id semantic mismatch",
            ),
            (
                "Open MB.",
                "Home - Limitless - Stripe - Memory usage - 687 MB",
                "target_id semantic mismatch",
            ),
            (
                "Open 687.",
                "Home - Limitless - Stripe - Memory usage - 687 MB",
                "target_id semantic mismatch",
            ),
            (
                "Open memory.",
                "Billing overview - OpenAI API - Memory usage - 99.2 MB",
                "target_id semantic mismatch",
            ),
            (
                "Open usage.",
                "Billing overview - OpenAI API - Memory usage - 99.2 MB",
                "target_id semantic mismatch",
            ),
            (
                "Open MB.",
                "Billing overview - OpenAI API - Memory usage - 99.2 MB",
                "target_id semantic mismatch",
            ),
            (
                "Open 99.",
                "Billing overview - OpenAI API - Memory usage - 99.2 MB",
                "target_id semantic mismatch",
            ),
            ("Open Stripe.", "Home - Limitless - Stripe - Memory usage - 687 MB", ""),
            ("Open Limitless.", "Home - Limitless - Stripe - Memory usage - 687 MB", ""),
            ("Open OpenAI API.", "Billing overview - OpenAI API - Memory usage - 99.2 MB", ""),
        )
        for instruction, label, reason in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            label,
                            "tabitem",
                            (120, 160, 220, 32),
                            window_title="GitHub Dashboard - Google Chrome",
                        )
                    ],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, reason)

    def test_chrome_tab_memory_usage_suffix_is_not_text_match_evidence(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        cases = (
            ("Open memory.", "Home - Limitless - Stripe - Memory usage - 687 MB"),
            ("Open usage.", "Home - Limitless - Stripe - Memory usage - 687 MB"),
            ("Open MB.", "Billing overview - OpenAI API - Memory usage - 99.2 MB"),
            ("Open 99.", "Billing overview - OpenAI API - Memory usage - 99.2 MB"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                result = resolve_candidate_target(
                    target_id="",
                    instruction=instruction,
                    candidates=[
                        ControlCandidate(
                            "c001",
                            label,
                            "tabitem",
                            (120, 160, 220, 32),
                            window_title="GitHub Dashboard - Google Chrome",
                        )
                    ],
                )

                self.assertIsNone(result)

    def test_browser_tab_generic_page_sections_are_not_title_evidence(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            (
                "Open home.",
                "Home - Limitless - Stripe - Memory usage - 687 MB",
                "target_id semantic mismatch",
            ),
            (
                "Click home.",
                "Home - Limitless - Stripe - Memory usage - 687 MB",
                "target_id semantic mismatch",
            ),
            (
                "Open overview.",
                "Billing overview - OpenAI API - Memory usage - 195 MB",
                "target_id semantic mismatch",
            ),
            (
                "Click overview.",
                "Billing overview - OpenAI API - Memory usage - 195 MB",
                "target_id semantic mismatch",
            ),
            ("Open Stripe tab.", "Home - Limitless - Stripe - Memory usage - 687 MB", ""),
            ("Open OpenAI API tab.", "Billing overview - OpenAI API - Memory usage - 195 MB", ""),
            ("Open home tab.", "Home - Limitless - Stripe - Memory usage - 687 MB", ""),
        )
        for instruction, label, reason in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            label,
                            "tabitem",
                            (120, 160, 220, 32),
                            window_title="GitHub Dashboard - Google Chrome",
                        )
                    ],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, reason)

    def test_browser_tab_generic_page_sections_are_not_text_match_evidence(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        cases = (
            ("Open home.", "Home - Limitless - Stripe - Memory usage - 687 MB"),
            ("Open overview.", "Billing overview - OpenAI API - Memory usage - 195 MB"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                result = resolve_candidate_target(
                    target_id="",
                    instruction=instruction,
                    candidates=[
                        ControlCandidate(
                            "c001",
                            label,
                            "tabitem",
                            (120, 160, 220, 32),
                            window_title="GitHub Dashboard - Google Chrome",
                        )
                    ],
                )

        self.assertIsNone(result)

    def test_browser_tab_generic_section_with_chrome_context_does_not_steal_text_match(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate(
                "tab",
                "Settings - Google Chrome",
                "tabitem",
                (80, 0, 220, 40),
                window_title="Settings - Google Chrome",
            ),
            ControlCandidate(
                "settings",
                "Settings",
                "button",
                (500, 120, 100, 32),
                window_title="Settings - Google Chrome",
            ),
        ]
        instruction = "Open Chrome settings."

        text_target = resolve_candidate_target(
            target_id="",
            instruction=instruction,
            candidates=candidates,
            model_rect=(500, 120, 100, 32),
        )
        snap_target = snap_candidate_target(
            instruction=instruction,
            candidates=candidates,
            model_rect=(500, 120, 100, 32),
        )
        help_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": instruction,
                    "target": {"x": 500, "y": 120, "width": 100, "height": 32},
                }
            ),
            self._capture(),
            candidates,
        )

        for resolved in (text_target, snap_target, help_target):
            self.assertEqual(resolved.target_id, "settings")
            self.assertFalse(resolved.rejected_reason)
            self.assertEqual(resolved.rect, (500, 120, 100, 32))

    def test_browser_tab_login_title_does_not_match_generic_auth_action(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        label = "Log In | Mercury - Memory usage - 372 MB"
        cases = (
            ("Log in.", "target_id semantic mismatch"),
            ("Open login.", "target_id semantic mismatch"),
            ("Open Mercury tab.", ""),
            ("Click the Mercury tab.", ""),
        )
        for instruction, reason in cases:
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            label,
                            "tabitem",
                            (120, 160, 220, 32),
                            window_title="GitHub Dashboard - Google Chrome",
                        )
                    ],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, reason)

    def test_browser_tab_login_title_is_not_text_match_evidence(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="",
            instruction="Log in.",
            candidates=[
                ControlCandidate(
                    "c001",
                    "Log In | Mercury - Memory usage - 372 MB",
                    "tabitem",
                    (120, 160, 220, 32),
                    window_title="GitHub Dashboard - Google Chrome",
                )
            ],
        )

        self.assertIsNone(result)

    def test_chrome_tab_owner_account_segment_is_not_title_evidence(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        label = (
            "DNS | Records | limitles.dev | "
            "Abelnavarrocarreon@gmail.com's Account | Cloudflare - Memory usage - 580 MB"
        )
        cases = (
            ("Click the Account tab.", "target_id semantic mismatch"),
            ("Open account.", "target_id semantic mismatch"),
            ("Click Cloudflare tab.", ""),
            ("Click DNS records tab.", ""),
        )
        for instruction, reason in cases:
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            label,
                            "tabitem",
                            (120, 160, 220, 32),
                            window_title="GitHub Dashboard - Google Chrome",
                        )
                    ],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, reason)

    def test_chrome_tab_owner_account_segment_is_not_text_match_evidence(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="",
            instruction="Click the Account tab.",
            candidates=[
                ControlCandidate(
                    "c001",
                    "DNS | Records | limitles.dev | "
                    "Abelnavarrocarreon@gmail.com's Account | Cloudflare - "
                    "Memory usage - 580 MB",
                    "tabitem",
                    (120, 160, 220, 32),
                    window_title="GitHub Dashboard - Google Chrome",
                )
            ],
        )

        self.assertIsNone(result)

    def test_chrome_tab_memory_usage_suffix_rejects_model_rect_snap(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Open memory.", "Home - Limitless - Stripe - Memory usage - 687 MB"),
            ("Open usage.", "Billing overview - OpenAI API - Memory usage - 99.2 MB"),
            ("Open MB.", "Billing overview - OpenAI API - Memory usage - 99.2 MB"),
            ("Open 99.", "Billing overview - OpenAI API - Memory usage - 99.2 MB"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target": {"x": 120, "y": 160, "width": 220, "height": 32},
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            label,
                            "tabitem",
                            (120, 160, 220, 32),
                            window_title="GitHub Dashboard - Google Chrome",
                        )
                    ],
                )

                self.assertEqual(target.source, "candidate_snap")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, "candidate semantic mismatch")

    def test_browser_tab_generic_page_sections_reject_model_rect_snap(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Open home.", "Home - Limitless - Stripe - Memory usage - 687 MB"),
            ("Open overview.", "Billing overview - OpenAI API - Memory usage - 195 MB"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target": {"x": 120, "y": 160, "width": 220, "height": 32},
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            label,
                            "tabitem",
                            (120, 160, 220, 32),
                            window_title="GitHub Dashboard - Google Chrome",
                        )
                    ],
                )

                self.assertEqual(target.source, "candidate_snap")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, "candidate semantic mismatch")

    def test_chrome_tab_owner_account_segment_rejects_model_rect_snap(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click the Account tab.",
                    "target": {"x": 120, "y": 160, "width": 220, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate(
                    "c001",
                    "DNS | Records | limitles.dev | "
                    "Abelnavarrocarreon@gmail.com's Account | Cloudflare - "
                    "Memory usage - 580 MB",
                    "tabitem",
                    (120, 160, 220, 32),
                    window_title="GitHub Dashboard - Google Chrome",
                )
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.target_id, "c001")
        self.assertEqual(target.rejected_reason, "candidate semantic mismatch")

    def test_browser_tab_login_title_rejects_model_rect_snap(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Log in.",
                    "target": {"x": 120, "y": 160, "width": 220, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate(
                    "c001",
                    "Log In | Mercury - Memory usage - 372 MB",
                    "tabitem",
                    (120, 160, 220, 32),
                    window_title="GitHub Dashboard - Google Chrome",
                )
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.target_id, "c001")
        self.assertEqual(target.rejected_reason, "candidate semantic mismatch")

    def test_gmail_tab_target_id_wins_over_generic_mail_decoys(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target
        from screen import Capture

        gmail_tab = (
            "Recibidos (3.921) - abelvalencianacarreon@gmail.com - Gmail - "
            "Memory usage - 270 MB"
        )
        cloudflare_email_tab = (
            "DNS | Records | limitles.dev | Abelnavarrocarreon@gmail.com's Account | "
            "Cloudflare - Memory usage - 580 MB"
        )
        private_email_bookmark = (
            "Unnamed bookmark for https://privateemail.com/appsuite/#!!&app=io.ox/"
            "mail&folder=default0/INBOX"
        )
        candidates = [
            ControlCandidate(
                "c006",
                cloudflare_email_tab,
                "tabitem",
                (690, 0, 184, 41),
                window_title="GitHub Dashboard - Google Chrome",
            ),
            ControlCandidate(
                "c010",
                gmail_tab,
                "tabitem",
                (1357, 0, 184, 41),
                window_title="GitHub Dashboard - Google Chrome",
            ),
            ControlCandidate(
                "c042",
                private_email_bookmark,
                "button",
                (822, 86, 28, 28),
                window_title="GitHub Dashboard - Google Chrome",
            ),
        ]
        capture = Capture(
            png_bytes=b"png",
            width=1920,
            height=1080,
            monitor_left=0,
            monitor_top=0,
            scale=1.0,
        )
        for instruction in (
            "Open Gmail.",
            "Open the Gmail tab.",
            "Open inbox.",
            "Open mail.",
            "Open email.",
        ):
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c010",
                        }
                    ),
                    capture,
                    candidates,
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c010")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, (1357, 0, 184, 41))

    def test_gmail_tab_preference_does_not_make_private_mail_bookmark_gmail(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open Gmail.",
                    "target_id": "c001",
                }
            ),
            self._capture(),
            [
                ControlCandidate(
                    "c001",
                    "Unnamed bookmark for https://privateemail.com/appsuite/#!!&app=io.ox/"
                    "mail&folder=default0/INBOX",
                    "button",
                    (120, 160, 32, 32),
                    window_title="GitHub Dashboard - Google Chrome",
                )
            ],
        )

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.target_id, "c001")
        self.assertEqual(target.rejected_reason, "target_id semantic mismatch")

    def test_mail_icon_text_match_overrides_settings_geometry(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open email.",
                    "target": {"x": 300, "y": 160, "width": 120, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "\u2709", "button", (120, 160, 32, 32)),
                ControlCandidate("c002", "Settings", "button", (300, 160, 120, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (120, 160, 32, 32))

    def test_mail_aliases_do_not_cross_clipboard_or_email_field_actions(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Paste into the note.", "\u2709", "target_id semantic mismatch"),
            ("Open email.", "\U0001f4cb", "target_id semantic mismatch"),
            ("Type your email.", "\u2709", "target_id control type mismatch"),
        )
        for instruction, label, reason in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [ControlCandidate("c001", label, "button", (120, 160, 32, 32))],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, reason)

    def test_copy_action_alias_target_id_accepts_duplicate_button(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Copy this item.", "Duplicate"),
            ("Clone this item.", "Duplicate"),
            ("Duplicate this item.", "Copy"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [ControlCandidate("c001", label, "button", (120, 160, 100, 32))],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, (120, 160, 100, 32))

    def test_copy_action_alias_text_match_overrides_wrong_geometry(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Copy this item.",
                    "target": {"x": 300, "y": 160, "width": 100, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Duplicate", "button", (120, 160, 100, 32)),
                ControlCandidate("c002", "Cancel", "button", (300, 160, 100, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (120, 160, 100, 32))

    def test_copy_action_alias_rejects_ambiguous_copy_and_duplicate_actions(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Copy this item.",
                    "target_id": "c001",
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Duplicate", "button", (120, 160, 100, 32)),
                ControlCandidate("c002", "Copy", "button", (260, 160, 100, 32)),
            ],
        )

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.target_id, "c001")
        self.assertEqual(target.rejected_reason, "target_id ambiguous")

    def test_create_and_completion_alias_target_id_accepts_common_buttons(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Create item.", "Add"),
            ("New item.", "Add"),
            ("Add item.", "Create"),
            ("Finish setup.", "Done"),
            ("Complete setup.", "Done"),
            ("Click Done.", "Finish"),
            ("Done.", "\u2713"),
            ("Complete task.", "Check mark"),
            ("Click the check mark.", "\u2714"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [ControlCandidate("c001", label, "button", (120, 160, 120, 32))],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, (120, 160, 120, 32))

    def test_confirm_checkmark_target_id_accepts_icons_and_labels(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Confirm selection.", "\u2713", "button", (120, 160, 32, 32)),
            ("Click OK.", "\u2714", "button", (120, 160, 32, 32)),
            ("Apply changes.", "\u2705", "button", (120, 160, 32, 32)),
            ("Complete task.", "Check mark", "button", (120, 160, 120, 32)),
            ("Click the check mark.", "OK", "button", (120, 160, 80, 32)),
        )
        for instruction, label, control_type, rect in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [ControlCandidate("c001", label, control_type, rect)],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, rect)

    def test_confirm_checkmark_aliases_do_not_cross_checkbox_intents(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        checkbox_instruction = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Check this box.",
                    "target_id": "c001",
                }
            ),
            self._capture(),
            [ControlCandidate("c001", "\u2713", "button", (120, 160, 32, 32))],
        )
        confirm_instruction = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click OK.",
                    "target_id": "c001",
                }
            ),
            self._capture(),
            [ControlCandidate("c001", "\u2713", "checkbox", (120, 160, 32, 32))],
        )

        self.assertEqual(checkbox_instruction.source, "target_id")
        self.assertEqual(
            checkbox_instruction.rejected_reason,
            "target_id control type mismatch",
        )
        self.assertEqual(confirm_instruction.source, "target_id")
        self.assertEqual(confirm_instruction.rejected_reason, "target_id control type mismatch")

    def test_uncheck_option_rejects_same_label_radiobutton(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate("check", "Weekly", "checkbox", (100, 100, 160, 32)),
            ControlCandidate("radio", "Weekly", "radiobutton", (100, 150, 160, 32)),
        ]
        instruction = "Uncheck the Weekly option."

        wrong_target = resolve_candidate_target(
            target_id="radio",
            instruction=instruction,
            candidates=candidates,
            model_rect=(100, 150, 160, 32),
        )
        text_target = resolve_candidate_target(
            target_id="",
            instruction=instruction,
            candidates=candidates,
            model_rect=(100, 150, 160, 32),
        )
        snap_target = snap_candidate_target(
            instruction=instruction,
            candidates=candidates,
            model_rect=(100, 150, 160, 32),
        )
        help_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": instruction,
                    "target_id": "radio",
                    "target": {"x": 100, "y": 150, "width": 160, "height": 32},
                }
            ),
            self._capture(),
            candidates,
        )

        self.assertEqual(wrong_target.source, "target_id")
        self.assertEqual(wrong_target.target_id, "radio")
        self.assertEqual(wrong_target.rejected_reason, "target_id control type mismatch")
        self.assertEqual(text_target.source, "text_match")
        self.assertEqual(text_target.target_id, "check")
        self.assertFalse(text_target.rejected_reason)
        self.assertEqual(snap_target.target_id, "check")
        self.assertFalse(snap_target.rejected_reason)
        self.assertEqual(help_target.source, "text_match")
        self.assertEqual(help_target.target_id, "check")
        self.assertFalse(help_target.rejected_reason)
        self.assertEqual(help_target.rect, (100, 100, 160, 32))

    def test_explicit_radio_rejects_same_label_non_radio_targets(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from help_session import resolve_help_target

        wrong_cases = (
            ("combo", "combobox", (100, 150, 160, 32)),
            ("menu", "menuitem", (100, 150, 160, 32)),
        )
        for wrong_id, wrong_type, wrong_rect in wrong_cases:
            with self.subTest(wrong_type=wrong_type):
                candidates = [
                    ControlCandidate("radio", "Weekly", "radiobutton", (100, 100, 160, 32)),
                    ControlCandidate(wrong_id, "Weekly", wrong_type, wrong_rect),
                ]
                instruction = "Select Weekly radio."

                wrong_target = resolve_candidate_target(
                    target_id=wrong_id,
                    instruction=instruction,
                    candidates=candidates,
                    model_rect=wrong_rect,
                )
                text_target = resolve_candidate_target(
                    target_id="",
                    instruction=instruction,
                    candidates=candidates,
                    model_rect=wrong_rect,
                )
                snap_target = snap_candidate_target(
                    instruction=instruction,
                    candidates=candidates,
                    model_rect=wrong_rect,
                )
                help_target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": wrong_id,
                            "target": {
                                "x": wrong_rect[0],
                                "y": wrong_rect[1],
                                "width": wrong_rect[2],
                                "height": wrong_rect[3],
                            },
                        }
                    ),
                    self._capture(),
                    candidates,
                )

                self.assertEqual(wrong_target.source, "target_id")
                self.assertEqual(wrong_target.target_id, wrong_id)
                self.assertEqual(
                    wrong_target.rejected_reason,
                    "target_id control type mismatch",
                )
                self.assertEqual(text_target.source, "text_match")
                self.assertEqual(text_target.target_id, "radio")
                self.assertFalse(text_target.rejected_reason)
                self.assertEqual(snap_target.target_id, "radio")
                self.assertFalse(snap_target.rejected_reason)
                self.assertEqual(help_target.source, "text_match")
                self.assertEqual(help_target.target_id, "radio")
                self.assertFalse(help_target.rejected_reason)
                self.assertEqual(help_target.rect, (100, 100, 160, 32))

    def test_explicit_option_rejects_same_label_combobox_launcher(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from help_session import resolve_help_target

        cases = (
            ("radio", "radiobutton", (100, 150, 160, 32)),
            ("menu", "menuitem", (100, 150, 160, 32)),
        )
        for expected_id, expected_type, expected_rect in cases:
            with self.subTest(expected_type=expected_type):
                candidates = [
                    ControlCandidate("combo", "State", "combobox", (100, 100, 160, 32)),
                    ControlCandidate(expected_id, "State", expected_type, expected_rect),
                ]
                instruction = "Select the State option."

                wrong_target = resolve_candidate_target(
                    target_id="combo",
                    instruction=instruction,
                    candidates=candidates,
                    model_rect=(100, 100, 160, 32),
                )
                text_target = resolve_candidate_target(
                    target_id="",
                    instruction=instruction,
                    candidates=candidates,
                    model_rect=(100, 100, 160, 32),
                )
                snap_target = snap_candidate_target(
                    instruction=instruction,
                    candidates=candidates,
                    model_rect=(100, 100, 160, 32),
                )
                help_target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "combo",
                            "target": {"x": 100, "y": 100, "width": 160, "height": 32},
                        }
                    ),
                    self._capture(),
                    candidates,
                )

                self.assertEqual(wrong_target.source, "target_id")
                self.assertEqual(wrong_target.target_id, "combo")
                self.assertEqual(wrong_target.rejected_reason, "target_id control type mismatch")
                self.assertEqual(text_target.source, "text_match")
                self.assertEqual(text_target.target_id, expected_id)
                self.assertFalse(text_target.rejected_reason)
                self.assertEqual(snap_target.target_id, expected_id)
                self.assertFalse(snap_target.rejected_reason)
                self.assertEqual(help_target.source, "text_match")
                self.assertEqual(help_target.target_id, expected_id)
                self.assertFalse(help_target.rejected_reason)
                self.assertEqual(help_target.rect, expected_rect)

    def test_text_box_wording_rejects_same_label_non_input_target(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate("edit", "Email", "edit", (100, 100, 180, 32)),
            ControlCandidate("button", "Email", "button", (100, 150, 180, 32)),
        ]
        instruction = "Enter Email in the text box."

        wrong_target = resolve_candidate_target(
            target_id="button",
            instruction=instruction,
            candidates=candidates,
            model_rect=(100, 150, 180, 32),
        )
        text_target = resolve_candidate_target(
            target_id="",
            instruction=instruction,
            candidates=candidates,
            model_rect=(100, 150, 180, 32),
        )
        snap_target = snap_candidate_target(
            instruction=instruction,
            candidates=candidates,
            model_rect=(100, 150, 180, 32),
        )
        help_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": instruction,
                    "target_id": "button",
                    "target": {"x": 100, "y": 150, "width": 180, "height": 32},
                }
            ),
            self._capture(),
            candidates,
        )

        self.assertEqual(wrong_target.target_id, "button")
        self.assertEqual(wrong_target.rejected_reason, "target_id control type mismatch")
        for resolved in (text_target, snap_target, help_target):
            self.assertEqual(resolved.target_id, "edit")
            self.assertFalse(resolved.rejected_reason)
            self.assertEqual(resolved.rect, (100, 100, 180, 32))

    def test_menu_option_wording_rejects_same_label_radio_button(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate("radio", "Weekly", "radiobutton", (100, 100, 160, 32)),
            ControlCandidate("menu", "Weekly", "menuitem", (100, 150, 160, 32)),
        ]
        instruction = "Select Weekly option from the menu."

        wrong_target = resolve_candidate_target(
            target_id="radio",
            instruction=instruction,
            candidates=candidates,
            model_rect=(100, 100, 160, 32),
        )
        text_target = resolve_candidate_target(
            target_id="",
            instruction=instruction,
            candidates=candidates,
            model_rect=(100, 100, 160, 32),
        )
        snap_target = snap_candidate_target(
            instruction=instruction,
            candidates=candidates,
            model_rect=(100, 100, 160, 32),
        )
        help_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": instruction,
                    "target_id": "radio",
                    "target": {"x": 100, "y": 100, "width": 160, "height": 32},
                }
            ),
            self._capture(),
            candidates,
        )

        self.assertEqual(wrong_target.target_id, "radio")
        self.assertEqual(wrong_target.rejected_reason, "target_id control type mismatch")
        for resolved in (text_target, snap_target, help_target):
            self.assertEqual(resolved.target_id, "menu")
            self.assertFalse(resolved.rejected_reason)
            self.assertEqual(resolved.rect, (100, 150, 160, 32))

    def test_create_and_completion_alias_text_match_overrides_wrong_geometry(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            (
                "Create item.",
                ControlCandidate("c001", "Add", "button", (120, 160, 100, 32)),
                ControlCandidate("c002", "Cancel", "button", (300, 160, 100, 32)),
            ),
            (
                "Finish setup.",
                ControlCandidate("c001", "Done", "button", (120, 160, 100, 32)),
                ControlCandidate("c002", "Back", "button", (300, 160, 100, 32)),
            ),
            (
                "Apply changes.",
                ControlCandidate("c001", "\u2713", "button", (120, 160, 32, 32)),
                ControlCandidate("c002", "Cancel", "button", (300, 160, 100, 32)),
            ),
        )
        for instruction, expected, decoy in cases:
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target": {
                                "x": decoy.rect[0],
                                "y": decoy.rect[1],
                                "width": decoy.rect[2],
                                "height": decoy.rect[3],
                            },
                        }
                    ),
                    self._capture(),
                    [expected, decoy],
                )

                self.assertEqual(target.source, "text_match")
                self.assertEqual(target.target_id, expected.id)
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, expected.rect)

    def test_explicit_action_context_mismatches_reject_inventory_paths(self) -> None:
        from control_inventory import (
            ControlCandidate,
            resolve_candidate_target,
            snap_candidate_target,
        )

        cases = (
            ("Edit profile.", "View profile", "App"),
            ("Apply changes.", "Cancel changes", "App"),
            ("Cancel changes.", "Apply changes", "App"),
            ("Apply filter.", "Apply coupon", "App"),
            ("Sort A to Z.", "Z to A", "App"),
            ("Sort Z to A.", "A to Z", "App"),
            ("Search users.", "Filter users", "App"),
            ("Filter users.", "Search users", "App"),
            ("Download file.", "Export", "App"),
            ("Export file.", "Download", "App"),
            ("Import file.", "Upload", "App"),
            ("Upload file.", "Import", "App"),
            ("Copy link.", "Duplicate link", "App"),
            ("Copy selected text.", "Duplicate text", "App"),
            ("Open new tab.", "New window", "GitHub - Google Chrome"),
            ("Open new window.", "New tab", "GitHub - Google Chrome"),
            ("Open file.", "Save file", "App"),
            ("Open file.", "Export file", "App"),
            ("Open file.", "Import file", "App"),
            ("Favorite this item.", "Bookmark this tab", "GitHub - Google Chrome"),
            ("Star this item.", "Bookmark this tab", "GitHub - Google Chrome"),
            ("Bookmark this item.", "Bookmark this tab", "GitHub - Google Chrome"),
            ("Open account.", "Delete account", "App"),
            ("Open account.", "Archive account", "App"),
            ("Open account.", "Create account", "App"),
            ("Open report.", "Download report", "App"),
            ("Show invoice.", "Delete invoice", "App"),
            ("View invoice.", "Pay invoice", "App"),
            ("View project.", "Archive project", "App"),
            ("Open message.", "Send message", "App"),
            ("Display account.", "Delete account", "App"),
            ("Inspect report.", "Download report", "App"),
            ("Read invoice.", "Pay invoice", "App"),
            ("Review project.", "Share project", "App"),
            ("See message.", "Send message", "App"),
            ("Click account.", "Delete account", "App"),
            ("Tap account.", "Archive account", "App"),
            ("Go to report.", "Download report", "App"),
            ("Click message.", "Send message", "App"),
            ("Click invoice.", "Pay invoice", "App"),
            ("Click project.", "Share project", "App"),
            ("Find account.", "Delete account", "App"),
            ("Search account.", "Archive account", "App"),
            ("Search for account.", "Delete account", "App"),
            ("Look for report.", "Download report", "App"),
            ("Focus account.", "Delete account", "App"),
            ("Delete account.", "Delete message", "App"),
            ("Download report.", "Download invoice", "App"),
            ("Copy coupon.", "Copy address", "App"),
            ("Send invite.", "Send email", "App"),
            ("Save document.", "Save profile", "App"),
            ("Save card.", "Save profile", "App"),
            ("Delete section.", "Delete account", "App"),
            ("Archive card.", "Archive email", "App"),
            ("Delete account.", "Delete", "Messages"),
            ("Show sidebar.", "Hide sidebar", "App"),
            ("Hide sidebar.", "Show sidebar", "App"),
            ("Open details.", "Close details", "App"),
            ("Close details.", "Open details", "App"),
            ("Open details.", "Edit details", "App"),
            ("Open profile.", "Edit profile", "App"),
            ("Mute speaker.", "Unmuted speaker", "App"),
            ("Mute speaker.", "Muted speaker", "App"),
            ("Show sidebar.", "Visible sidebar", "App"),
            ("Expand Advanced settings.", "Collapsed Advanced settings", "App"),
            ("Expand Advanced settings.", "Expanded Advanced settings", "App"),
            ("Lock account.", "Unlocked account", "App"),
            ("Lock account.", "Locked account", "App"),
            ("Archive email.", "Unarchived email", "App"),
            ("Archive email.", "Archived email", "App"),
            ("Start recording.", "Stopped recording", "App"),
            ("Start recording.", "Started recording", "App"),
            ("Mute microphone.", "Unmute microphone", "App"),
            ("Unmute microphone.", "Mute microphone", "App"),
            ("Lock account.", "Unlock account", "App"),
            ("Unlock account.", "Lock account", "App"),
            ("Archive email.", "Unarchive email", "App"),
            ("Unarchive email.", "Archive email", "App"),
            ("Subscribe channel.", "Unsubscribe channel", "App"),
            ("Unsubscribe channel.", "Subscribe channel", "App"),
            ("Connect account.", "Disconnect account", "App"),
            ("Disconnect account.", "Connect account", "App"),
            ("Start recording.", "Stop recording", "App"),
            ("Stop recording.", "Start recording", "App"),
        )
        for instruction, label, window_title in cases:
            with self.subTest(instruction=instruction, label=label):
                candidate = ControlCandidate(
                    "c001",
                    label,
                    "button",
                    (120, 160, 180, 32),
                    window_title=window_title,
                )

                text_target = resolve_candidate_target(
                    target_id="",
                    instruction=instruction,
                    candidates=[candidate],
                )
                target_id = resolve_candidate_target(
                    target_id="c001",
                    instruction=instruction,
                    candidates=[candidate],
                )
                snap_target = snap_candidate_target(
                    instruction=instruction,
                    candidates=[candidate],
                    model_rect=(120, 160, 180, 32),
                )

                self.assertIsNone(text_target)
                self.assertEqual(target_id.source, "target_id")
                self.assertEqual(target_id.rejected_reason, "target_id semantic mismatch")
                self.assertEqual(snap_target.source, "candidate_snap")
                self.assertEqual(snap_target.rejected_reason, "candidate semantic mismatch")

    def test_explicit_action_context_exact_targets_still_accept(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        cases = (
            ("Edit profile.", "Edit profile"),
            ("Apply filter.", "Apply filter"),
            ("Sort A to Z.", "A to Z"),
            ("Sort Z to A.", "Z to A"),
            ("Search users.", "Search users"),
            ("Filter users.", "Filter users"),
            ("Download file.", "Download"),
            ("Export file.", "Export"),
            ("Import file.", "Import"),
            ("Upload file.", "Upload"),
            ("Copy link.", "Copy link"),
            ("Copy selected text.", "Copy"),
            ("Open new tab.", "New tab"),
            ("Open new window.", "New window"),
            ("Open file.", "Open file"),
            ("Bookmark this tab.", "Bookmark this tab"),
            ("Add bookmark.", "Bookmark this tab"),
            ("Save document.", "Save file"),
            ("Save card.", "Save card"),
            ("Delete account.", "Delete"),
            ("Delete account.", "Delete", "Accounts"),
            ("Delete section.", "Delete section"),
            ("Copy coupon.", "Copy"),
            ("Archive card.", "Archive card"),
            ("Show sidebar.", "Show sidebar"),
            ("Hide sidebar.", "Hide sidebar"),
            ("Open details.", "Open details"),
            ("Display account.", "Account"),
            ("Inspect report.", "Report"),
            ("Read invoice.", "Invoice"),
            ("Review project.", "Project"),
            ("See message.", "Message"),
            ("Close details.", "Close details"),
            ("Mute microphone.", "Mute microphone"),
            ("Unmute microphone.", "Unmute microphone"),
            ("Archive email.", "Archive email"),
            ("Unarchive email.", "Unarchive email"),
            ("Unlock account.", "Lock"),
            ("Click Delete account.", "Delete account"),
            ("Click checkout.", "Checkout"),
            ("Click Pandora.", "New Pandora (1)"),
            ("Find account.", "Find account"),
            ("Search account.", "Search account"),
            ("Focus account.", "Account"),
        )
        for case in cases:
            if len(case) == 2:
                instruction, label = case
                window_title = "GitHub - Google Chrome"
            else:
                instruction, label, window_title = case
            with self.subTest(instruction=instruction, label=label):
                target = resolve_candidate_target(
                    target_id="c001",
                    instruction=instruction,
                    candidates=[
                        ControlCandidate(
                            "c001",
                            label,
                            "button",
                            (120, 160, 180, 32),
                            window_title=window_title,
                        )
                    ],
                )

                self.assertEqual(target.source, "target_id")
                self.assertFalse(target.rejected_reason)

    def test_state_word_in_exact_object_label_still_accepts_target(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate(
                "closed_tab",
                "Closed tickets",
                "tabitem",
                (100, 100, 150, 36),
                window_title="Helpdesk",
            ),
            ControlCandidate(
                "open_tab",
                "Open tickets",
                "tabitem",
                (260, 100, 140, 36),
                window_title="Helpdesk",
            ),
        ]
        instruction = "Open Closed tickets."

        target_id = resolve_candidate_target(
            target_id="closed_tab",
            instruction=instruction,
            candidates=candidates,
            model_rect=(100, 100, 150, 36),
        )
        text_target = resolve_candidate_target(
            target_id="",
            instruction=instruction,
            candidates=candidates,
            model_rect=(100, 100, 150, 36),
        )
        snap_target = snap_candidate_target(
            instruction=instruction,
            candidates=candidates,
            model_rect=(100, 100, 150, 36),
        )
        help_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": instruction,
                    "target_id": "closed_tab",
                    "target": {"x": 100, "y": 100, "width": 150, "height": 36},
                }
            ),
            self._capture(),
            candidates,
        )

        self.assertEqual(target_id.source, "target_id")
        self.assertEqual(target_id.target_id, "closed_tab")
        self.assertFalse(target_id.rejected_reason)
        self.assertEqual(text_target.target_id, "closed_tab")
        self.assertFalse(text_target.rejected_reason)
        self.assertEqual(snap_target.target_id, "closed_tab")
        self.assertFalse(snap_target.rejected_reason)
        self.assertEqual(help_target.source, "target_id")
        self.assertEqual(help_target.target_id, "closed_tab")
        self.assertFalse(help_target.rejected_reason)
        self.assertEqual(help_target.rect, (100, 100, 150, 36))

    def test_generic_action_snap_checks_containing_row_context(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate("r1", "Profile", "listitem", (100, 40, 520, 72), window_title="App"),
            ControlCandidate("p", "Edit", "button", (560, 60, 60, 32), window_title="App"),
            ControlCandidate("r2", "Message from Alice", "listitem", (100, 120, 520, 72), window_title="App"),
            ControlCandidate("m", "Edit", "button", (560, 140, 60, 32), window_title="App"),
        ]

        wrong_snap = snap_candidate_target(
            instruction="Edit profile.",
            candidates=candidates,
            model_rect=(560, 140, 60, 32),
        )
        right_snap = snap_candidate_target(
            instruction="Edit profile.",
            candidates=candidates,
            model_rect=(560, 60, 60, 32),
        )
        wrong_help = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Edit profile.",
                    "target": {"x": 560, "y": 140, "width": 60, "height": 32},
                }
            ),
            self._capture(),
            candidates,
        )

        self.assertIsNone(wrong_snap)
        self.assertEqual(right_snap.source, "candidate_snap")
        self.assertEqual(right_snap.target_id, "p")
        self.assertFalse(right_snap.rejected_reason)
        self.assertEqual(wrong_help.source, "candidate_snap")
        self.assertFalse(wrong_help.target_id)
        self.assertEqual(wrong_help.rejected_reason, "candidate snapshot no match")

    def test_contextual_duplicate_requires_container_evidence(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from help_session import resolve_help_target

        ambiguous_candidates = [
            ControlCandidate("c001", "Save", "button", (150, 24, 72, 32)),
            ControlCandidate("c002", "Save", "button", (150, 124, 72, 32)),
        ]
        contextual_candidates = [
            ControlCandidate("profile", "Profile", "listitem", (100, 0, 220, 80)),
            ControlCandidate("c001", "Save", "button", (150, 24, 72, 32)),
            ControlCandidate("billing", "Billing", "listitem", (100, 100, 220, 80)),
            ControlCandidate("c002", "Save", "button", (150, 124, 72, 32)),
        ]

        target_id = resolve_candidate_target(
            target_id="c001",
            instruction="Click Save in the Billing card.",
            candidates=ambiguous_candidates,
            model_rect=(150, 24, 72, 32),
        )
        text_target = resolve_candidate_target(
            target_id="",
            instruction="Click Save in the Billing card.",
            candidates=ambiguous_candidates,
            model_rect=(150, 24, 72, 32),
        )
        snap_target = snap_candidate_target(
            instruction="Click Save in the Billing card.",
            candidates=ambiguous_candidates,
            model_rect=(150, 24, 72, 32),
        )
        help_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Save in the Billing card.",
                    "target": {"x": 150, "y": 24, "width": 72, "height": 32},
                }
            ),
            self._capture(),
            ambiguous_candidates,
        )
        contextual_snap = snap_candidate_target(
            instruction="Click Save in the Billing card.",
            candidates=contextual_candidates,
            model_rect=(150, 124, 72, 32),
        )

        self.assertEqual(target_id.source, "target_id")
        self.assertEqual(target_id.rejected_reason, "target_id ambiguous")
        self.assertIsNone(text_target)
        self.assertIsNone(snap_target)
        self.assertEqual(help_target.source, "candidate_snap")
        self.assertEqual(help_target.rejected_reason, "candidate snapshot no match")
        self.assertEqual(contextual_snap.source, "candidate_snap")
        self.assertEqual(contextual_snap.target_id, "c002")
        self.assertFalse(contextual_snap.rejected_reason)

    def test_explicit_row_request_is_not_demoted_to_same_label_child_button(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate("row", "Settings", "listitem", (10, 10, 600, 80)),
            ControlCandidate("btn", "Settings", "button", (20, 20, 80, 32)),
        ]

        target_id = resolve_candidate_target(
            target_id="row",
            instruction="Click the Settings table row.",
            candidates=candidates,
            model_rect=(10, 10, 600, 80),
        )
        text_target = resolve_candidate_target(
            target_id="",
            instruction="Click the Settings table row.",
            candidates=candidates,
            model_rect=(10, 10, 600, 80),
        )
        snap_target = snap_candidate_target(
            instruction="Click the Settings table row.",
            candidates=candidates,
            model_rect=(10, 10, 600, 80),
        )
        help_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click the Settings table row.",
                    "target_id": "row",
                    "target": {"x": 10, "y": 10, "width": 600, "height": 80},
                }
            ),
            self._capture(),
            candidates,
        )

        for target in (target_id, text_target, snap_target, help_target):
            self.assertIsNotNone(target)
            assert target is not None
            self.assertEqual(target.target_id, "row")
            self.assertEqual(target.rect, (10, 10, 600, 80))
            self.assertFalse(target.rejected_reason)

    def test_explicit_card_request_is_not_demoted_to_same_label_child_button(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate("card", "Settings", "listitem", (10, 10, 600, 80)),
            ControlCandidate("btn", "Settings", "button", (20, 20, 80, 32)),
        ]

        target_id = resolve_candidate_target(
            target_id="card",
            instruction="Click the Settings card.",
            candidates=candidates,
            model_rect=(10, 10, 600, 80),
        )
        snap_target = snap_candidate_target(
            instruction="Click the Settings card.",
            candidates=candidates,
            model_rect=(10, 10, 600, 80),
        )
        help_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click the Settings card.",
                    "target_id": "card",
                    "target": {"x": 10, "y": 10, "width": 600, "height": 80},
                }
            ),
            self._capture(),
            candidates,
        )

        for target in (target_id, snap_target, help_target):
            self.assertEqual(target.target_id, "card")
            self.assertEqual(target.rect, (10, 10, 600, 80))
            self.assertFalse(target.rejected_reason)

    def test_contextual_duplicate_row_name_rejects_wrong_row_action(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target

        candidates = [
            ControlCandidate("alice_row", "Alice", "listitem", (10, 10, 600, 80)),
            ControlCandidate("alice_edit", "Edit", "button", (520, 34, 60, 32)),
            ControlCandidate("bob_row", "Bob", "listitem", (10, 100, 600, 80)),
            ControlCandidate("bob_edit", "Edit", "button", (520, 124, 60, 32)),
        ]

        wrong_target = resolve_candidate_target(
            target_id="bob_edit",
            instruction="Edit Alice row.",
            candidates=candidates,
            model_rect=(520, 124, 60, 32),
        )
        wrong_snap = snap_candidate_target(
            instruction="Edit Alice row.",
            candidates=candidates,
            model_rect=(520, 124, 60, 32),
        )
        right_target = resolve_candidate_target(
            target_id="alice_edit",
            instruction="Edit Alice row.",
            candidates=candidates,
            model_rect=(520, 34, 60, 32),
        )

        self.assertEqual(wrong_target.target_id, "bob_edit")
        self.assertEqual(wrong_target.rejected_reason, "target_id ambiguous")
        self.assertEqual(wrong_snap.target_id, "alice_edit")
        self.assertFalse(wrong_snap.rejected_reason)
        self.assertEqual(right_target.target_id, "alice_edit")
        self.assertFalse(right_target.rejected_reason)

    def test_contextual_duplicate_static_label_rejects_wrong_row_action(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate("label_acme", "Acme", "text", (20, 90, 120, 30)),
            ControlCandidate("approve_acme", "Approve", "button", (180, 90, 90, 30)),
            ControlCandidate("label_globex", "Globex", "text", (20, 140, 120, 30)),
            ControlCandidate("approve_globex", "Approve", "button", (180, 140, 90, 30)),
        ]
        instructions = (
            "Approve Acme.",
            "Approve Acme request.",
            "Approve request for Acme.",
        )

        for instruction in instructions:
            with self.subTest(instruction=instruction):
                wrong_target = resolve_candidate_target(
                    target_id="approve_globex",
                    instruction=instruction,
                    candidates=candidates,
                    model_rect=(180, 140, 90, 30),
                )
                text_target = resolve_candidate_target(
                    target_id="",
                    instruction=instruction,
                    candidates=candidates,
                    model_rect=(180, 140, 90, 30),
                )
                wrong_snap = snap_candidate_target(
                    instruction=instruction,
                    candidates=candidates,
                    model_rect=(180, 140, 90, 30),
                )
                help_target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "approve_globex",
                            "target": {"x": 180, "y": 140, "width": 90, "height": 30},
                        }
                    ),
                    self._capture(),
                    candidates,
                )

                self.assertEqual(wrong_target.target_id, "approve_globex")
                self.assertEqual(wrong_target.rejected_reason, "target_id ambiguous")
                self.assertEqual(text_target.target_id, "approve_acme")
                self.assertFalse(text_target.rejected_reason)
                self.assertEqual(wrong_snap.target_id, "approve_acme")
                self.assertFalse(wrong_snap.rejected_reason)
                self.assertEqual(help_target.source, "text_match")
                self.assertEqual(help_target.target_id, "approve_acme")
                self.assertFalse(help_target.rejected_reason)
                self.assertEqual(help_target.rect, (180, 90, 90, 30))

    def test_contextual_duplicate_cell_label_rejects_wrong_row_action(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from help_session import resolve_help_target

        instruction = "Approve Nimbus."

        for label_type in ("cell", "datagridcell", "rowheader"):
            with self.subTest(label_type=label_type):
                candidates = [
                    ControlCandidate("cell_nimbus", "Nimbus", label_type, (20, 90, 140, 30)),
                    ControlCandidate("approve_nimbus", "Approve", "button", (200, 90, 90, 30)),
                    ControlCandidate("cell_orion", "Orion", label_type, (20, 140, 140, 30)),
                    ControlCandidate("approve_orion", "Approve", "button", (200, 140, 90, 30)),
                ]

                wrong_target = resolve_candidate_target(
                    target_id="approve_orion",
                    instruction=instruction,
                    candidates=candidates,
                    model_rect=(200, 140, 90, 30),
                )
                text_target = resolve_candidate_target(
                    target_id="",
                    instruction=instruction,
                    candidates=candidates,
                    model_rect=(200, 140, 90, 30),
                )
                wrong_snap = snap_candidate_target(
                    instruction=instruction,
                    candidates=candidates,
                    model_rect=(200, 140, 90, 30),
                )
                help_target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "approve_orion",
                            "target": {"x": 200, "y": 140, "width": 90, "height": 30},
                        }
                    ),
                    self._capture(),
                    candidates,
                )

                self.assertEqual(wrong_target.target_id, "approve_orion")
                self.assertEqual(wrong_target.rejected_reason, "target_id ambiguous")
                self.assertEqual(text_target.target_id, "approve_nimbus")
                self.assertFalse(text_target.rejected_reason)
                self.assertEqual(wrong_snap.target_id, "approve_nimbus")
                self.assertFalse(wrong_snap.rejected_reason)
                self.assertEqual(help_target.source, "text_match")
                self.assertEqual(help_target.target_id, "approve_nimbus")
                self.assertFalse(help_target.rejected_reason)
                self.assertEqual(help_target.rect, (200, 90, 90, 30))

    def test_row_scoped_clear_field_rejects_wrong_row_action(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate("alice_row", "Alice", "listitem", (20, 80, 760, 56)),
            ControlCandidate("alice_email", "Email", "edit", (120, 92, 300, 32)),
            ControlCandidate("alice_clear", "Clear", "button", (390, 94, 28, 28)),
            ControlCandidate("bob_row", "Bob", "listitem", (20, 156, 760, 56)),
            ControlCandidate("bob_email", "Email", "edit", (120, 168, 300, 32)),
            ControlCandidate("bob_clear", "Clear", "button", (390, 170, 28, 28)),
        ]
        instructions = (
            "Clear the Email field in Bob row.",
            "Clear the Email field in row Bob.",
        )

        for instruction in instructions:
            with self.subTest(instruction=instruction):
                wrong_target = resolve_candidate_target(
                    target_id="alice_clear",
                    instruction=instruction,
                    candidates=candidates,
                    model_rect=(390, 94, 28, 28),
                )
                wrong_snap = snap_candidate_target(
                    instruction=instruction,
                    candidates=candidates,
                    model_rect=(390, 94, 28, 28),
                )
                right_target = resolve_candidate_target(
                    target_id="bob_clear",
                    instruction=instruction,
                    candidates=candidates,
                    model_rect=(390, 170, 28, 28),
                )
                help_target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "alice_clear",
                            "target": {"x": 390, "y": 94, "width": 28, "height": 28},
                        }
                    ),
                    self._capture(),
                    candidates,
                )

                self.assertEqual(wrong_target.target_id, "alice_clear")
                self.assertIn(
                    wrong_target.rejected_reason,
                    {"target_id ambiguous", "target_id semantic mismatch"},
                )
                self.assertEqual(wrong_snap.target_id, "bob_clear")
                self.assertFalse(wrong_snap.rejected_reason)
                self.assertEqual(right_target.target_id, "bob_clear")
                self.assertFalse(right_target.rejected_reason)
                self.assertEqual(help_target.source, "text_match")
                self.assertEqual(help_target.target_id, "bob_clear")
                self.assertEqual(help_target.rect, (390, 170, 28, 28))

    def test_turn_on_off_checkbox_polarity_rejects_opposite_label(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target

        cases = (
            ("Turn on notifications.", "Turn off notifications"),
            ("Turn off notifications.", "Turn on notifications"),
            ("Enable notifications.", "Turn off notifications"),
            ("Turn on notifications.", "Disable notifications"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                candidate = ControlCandidate(
                    "c001",
                    label,
                    "checkbox",
                    (120, 160, 220, 32),
                    window_title="App",
                )

                text_target = resolve_candidate_target(
                    target_id="",
                    instruction=instruction,
                    candidates=[candidate],
                )
                target_id = resolve_candidate_target(
                    target_id="c001",
                    instruction=instruction,
                    candidates=[candidate],
                )
                snap_target = snap_candidate_target(
                    instruction=instruction,
                    candidates=[candidate],
                    model_rect=(120, 160, 220, 32),
                )

                self.assertIsNone(text_target)
                self.assertEqual(target_id.source, "target_id")
                self.assertEqual(target_id.rejected_reason, "target_id semantic mismatch")
                self.assertEqual(snap_target.source, "candidate_snap")
                self.assertEqual(snap_target.rejected_reason, "candidate semantic mismatch")

    def test_action_requests_reject_state_status_labels(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target

        cases = (
            ("Apply changes.", "Applied changes", "button"),
            ("Apply changes.", "Changes applied", "button"),
            ("Confirm selection.", "Confirmed selection", "button"),
            ("Complete task.", "Completed task", "button"),
            ("Complete task.", "Task completed", "button"),
            ("Finish setup.", "Finished setup", "button"),
            ("Click OK.", "OK status", "button"),
            ("Cancel subscription.", "Cancelled subscription", "button"),
            ("Cancel subscription.", "Subscription canceled", "button"),
            ("Create account.", "Created account", "button"),
            ("Delete file.", "Deleted file", "button"),
            ("Delete file.", "File deleted", "button"),
            ("Download report.", "Downloaded report", "button"),
            ("Download report.", "Report downloaded", "button"),
            ("Dismiss notification.", "Dismissed notification", "button"),
            ("Filter orders.", "Orders filtered", "button"),
            ("Install app.", "Installed app", "button"),
            ("Invite user.", "Invited user", "button"),
            ("Fix issue.", "Fixed issue", "button"),
            ("Resolve alert.", "Resolved alert", "button"),
            ("Paste message.", "Message pasted", "button"),
            ("Print document.", "Document printed", "button"),
            ("Save document.", "Saved document", "button"),
            ("Save document.", "Document saved", "button"),
            ("Save document.", "Document autosaved", "button"),
            ("Search orders.", "Orders searched", "button"),
            ("Send message.", "Sent message", "button"),
            ("Send message.", "Message delivered", "button"),
            ("Share link.", "Shared link", "button"),
            ("Sort orders.", "Orders sorted", "button"),
            ("Sync files.", "Files synced", "button"),
            ("Update profile.", "Updated profile", "button"),
            ("Upload file.", "Uploaded file", "button"),
            ("Upload file.", "File uploaded", "button"),
            ("Enable notifications.", "Enabled notifications", "checkbox"),
            ("Enable notifications.", "Disabled notifications", "checkbox"),
            ("Disable notifications.", "Disabled notifications", "checkbox"),
            ("Disable notifications.", "Enabled notifications", "checkbox"),
            ("Turn on notifications.", "Enabled notifications", "checkbox"),
            ("Turn on notifications.", "Disabled notifications", "checkbox"),
            ("Turn off notifications.", "Disabled notifications", "checkbox"),
            ("Turn off notifications.", "Enabled notifications", "checkbox"),
            ("Mute speaker.", "Muted speaker", "button"),
            ("Mute speaker.", "Unmuted speaker", "button"),
            ("Unmute speaker.", "Unmuted speaker", "button"),
            ("Unmute speaker.", "Muted speaker", "button"),
            ("Show sidebar.", "Visible sidebar", "button"),
            ("Show sidebar.", "Hidden sidebar", "button"),
            ("Expand Advanced settings.", "Expanded Advanced settings", "button"),
            ("Expand Advanced settings.", "Collapsed Advanced settings", "button"),
            ("Collapse Advanced settings.", "Collapsed Advanced settings", "button"),
            ("Collapse Advanced settings.", "Expanded Advanced settings", "button"),
            ("Lock account.", "Locked account", "button"),
            ("Lock account.", "Unlocked account", "button"),
            ("Unlock account.", "Unlocked account", "button"),
            ("Unlock account.", "Locked account", "button"),
            ("Connect account.", "Connected account", "button"),
            ("Connect account.", "Disconnected account", "button"),
            ("Disconnect account.", "Disconnected account", "button"),
            ("Disconnect account.", "Connected account", "button"),
            ("Activate notifications.", "Activated notifications", "button"),
            ("Activate notifications.", "Deactivated notifications", "button"),
            ("Deactivate notifications.", "Deactivated notifications", "button"),
            ("Deactivate notifications.", "Activated notifications", "button"),
            ("Archive email.", "Archived email", "button"),
            ("Archive email.", "Unarchived email", "button"),
            ("Unarchive email.", "Unarchived email", "button"),
            ("Unarchive email.", "Archived email", "button"),
            ("Start recording.", "Started recording", "button"),
            ("Start recording.", "Stopped recording", "button"),
            ("Stop recording.", "Stopped recording", "button"),
            ("Stop recording.", "Started recording", "button"),
            ("Approve request.", "Approved request", "button"),
            ("Reject request.", "Rejected request", "button"),
            ("Mark as read.", "Read message", "button"),
            ("Mark as unread.", "Unread message", "button"),
        )
        for instruction, label, control_type in cases:
            with self.subTest(instruction=instruction, label=label):
                candidate = ControlCandidate(
                    "c001",
                    label,
                    control_type,
                    (120, 160, 240, 32),
                    window_title="App",
                )

                text_target = resolve_candidate_target(
                    target_id="",
                    instruction=instruction,
                    candidates=[candidate],
                )
                target_id = resolve_candidate_target(
                    target_id="c001",
                    instruction=instruction,
                    candidates=[candidate],
                )
                snap_target = snap_candidate_target(
                    instruction=instruction,
                    candidates=[candidate],
                    model_rect=(120, 160, 240, 32),
                )

                self.assertIsNone(text_target)
                self.assertEqual(target_id.source, "target_id")
                self.assertEqual(target_id.rejected_reason, "target_id semantic mismatch")
                self.assertEqual(snap_target.source, "candidate_snap")
                self.assertEqual(snap_target.rejected_reason, "candidate semantic mismatch")

    def test_generic_search_rejects_search_results_labels(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target

        cases = (
            ControlCandidate("c001", "Search results", "button", (120, 160, 180, 32)),
            ControlCandidate("c001", "Search results", "headeritem", (120, 160, 180, 32)),
        )
        for candidate in cases:
            with self.subTest(control_type=candidate.control_type):
                text_target = resolve_candidate_target(
                    target_id="",
                    instruction="Open search.",
                    candidates=[candidate],
                )
                target_id = resolve_candidate_target(
                    target_id="c001",
                    instruction="Open search.",
                    candidates=[candidate],
                )
                snap_target = snap_candidate_target(
                    instruction="Open search.",
                    candidates=[candidate],
                    model_rect=(120, 160, 180, 32),
                )

                self.assertIsNone(text_target)
                self.assertEqual(target_id.source, "target_id")
                self.assertEqual(target_id.rejected_reason, "target_id semantic mismatch")
                self.assertEqual(snap_target.source, "candidate_snap")
                self.assertEqual(snap_target.rejected_reason, "candidate semantic mismatch")

    def test_sign_out_alias_target_id_accepts_logout_button(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Sign out.", "Logout"),
            ("Log out.", "Sign out"),
            ("Click logout.", "Sign out"),
            ("Sign in.", "Log in"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [ControlCandidate("c001", label, "button", (120, 160, 100, 32))],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, (120, 160, 100, 32))

    def test_sign_out_text_match_overrides_profile_and_sign_in_geometry(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            (
                ControlCandidate("c001", "Logout", "button", (120, 160, 100, 32)),
                ControlCandidate("c002", "Profile", "button", (300, 160, 100, 32)),
            ),
            (
                ControlCandidate("c001", "Logout", "button", (120, 160, 100, 32)),
                ControlCandidate("c002", "Sign in", "button", (300, 160, 100, 32)),
            ),
        )
        for expected, decoy in cases:
            with self.subTest(decoy=decoy.text):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": "Sign out.",
                            "target": {
                                "x": decoy.rect[0],
                                "y": decoy.rect[1],
                                "width": decoy.rect[2],
                                "height": decoy.rect[3],
                            },
                        }
                    ),
                    self._capture(),
                    [expected, decoy],
                )

                self.assertEqual(target.source, "text_match")
                self.assertEqual(target.target_id, expected.id)
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, expected.rect)

    def test_dialog_dismiss_target_id_accepts_contextual_cancel_and_close_buttons(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Close the dialog.", "Cancel"),
            ("Dismiss the dialog.", "Cancel"),
            ("Cancel the dialog.", "Close"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [ControlCandidate("c001", label, "button", (120, 160, 100, 32))],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, (120, 160, 100, 32))

    def test_dialog_dismiss_text_match_overrides_wrong_geometry(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Close the dialog.",
                    "target": {"x": 300, "y": 160, "width": 100, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Cancel", "button", (120, 160, 100, 32)),
                ControlCandidate("c002", "Details", "button", (300, 160, 100, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (120, 160, 100, 32))

    def test_dialog_dismiss_uses_exact_model_rect_among_duplicate_close_buttons(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate(
                "page_close",
                "Close",
                "button",
                (100, 100, 80, 32),
                window_title="Editor",
                window_rank=0,
            ),
            ControlCandidate(
                "dialog_close",
                "Close",
                "button",
                (500, 300, 80, 32),
                window_title="Preferences dialog",
                window_rank=1,
            ),
        ]
        target = resolve_candidate_target(
            target_id="",
            instruction="Close the dialog.",
            candidates=candidates,
            model_rect=(500, 300, 80, 32),
        )
        help_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Close the dialog.",
                    "target": {"x": 500, "y": 300, "width": 80, "height": 32},
                }
            ),
            self._capture(),
            candidates,
        )
        snap_target = snap_candidate_target(
            instruction="Close the dialog.",
            candidates=candidates,
            model_rect=(500, 300, 80, 32),
        )

        for resolved in (target, help_target):
            self.assertEqual(resolved.source, "text_match")
            self.assertEqual(resolved.target_id, "dialog_close")
            self.assertFalse(resolved.rejected_reason)
            self.assertEqual(resolved.rect, (500, 300, 80, 32))
        self.assertIsNone(snap_target)

    def test_dialog_dismiss_prefers_exact_action_when_cancel_and_close_exist(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Close the dialog.", "c002", (280, 160, 100, 32)),
            ("Cancel the dialog.", "c001", (120, 160, 100, 32)),
        )
        for instruction, target_id, rect in cases:
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target": {"x": 120, "y": 160, "width": 260, "height": 32},
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate("c001", "Cancel", "button", (120, 160, 100, 32)),
                        ControlCandidate("c002", "Close", "button", (280, 160, 100, 32)),
                    ],
                )

                self.assertEqual(target.source, "text_match")
                self.assertEqual(target.target_id, target_id)
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, rect)

    def test_domain_cancel_does_not_match_close_button(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Cancel subscription.",
                    "target_id": "c001",
                }
            ),
            self._capture(),
            [ControlCandidate("c001", "Close", "button", (120, 160, 100, 32))],
        )

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.target_id, "c001")
        self.assertEqual(target.rejected_reason, "target_id semantic mismatch")

    def test_clipboard_action_target_id_accepts_common_icon_labels(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Paste into the note.", "Clipboard", (120, 160, 110, 32)),
            ("Paste.", "\U0001f4cb", (120, 160, 32, 32)),
            ("Copy selected text.", "Copy", (120, 160, 80, 32)),
            ("Paste selected text.", "Paste", (120, 160, 80, 32)),
            ("Cut selection.", "Scissors", (120, 160, 100, 32)),
            ("Cut selection.", "\u2702", (120, 160, 32, 32)),
            ("Click scissors.", "Cut", (120, 160, 80, 32)),
        )
        for instruction, label, rect in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [ControlCandidate("c001", label, "button", rect)],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, rect)

    def test_clipboard_action_text_match_overrides_wrong_geometry(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            (
                "Paste.",
                ControlCandidate("c001", "Clipboard", "button", (120, 160, 110, 32)),
                ControlCandidate("c002", "Export", "button", (300, 160, 100, 32)),
            ),
            (
                "Copy selected text.",
                ControlCandidate("c001", "Copy", "button", (120, 160, 80, 32)),
                ControlCandidate("c002", "Body text", "edit", (300, 160, 220, 32)),
            ),
            (
                "Paste selected text.",
                ControlCandidate("c001", "Paste", "button", (120, 160, 80, 32)),
                ControlCandidate("c002", "Body text", "edit", (300, 160, 220, 32)),
            ),
            (
                "Cut selection.",
                ControlCandidate("c001", "Scissors", "button", (120, 160, 100, 32)),
                ControlCandidate("c002", "Copy", "button", (300, 160, 100, 32)),
            ),
        )
        for instruction, expected, decoy in cases:
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target": {
                                "x": decoy.rect[0],
                                "y": decoy.rect[1],
                                "width": decoy.rect[2],
                                "height": decoy.rect[3],
                            },
                        }
                    ),
                    self._capture(),
                    [expected, decoy],
                )

                self.assertEqual(target.source, "text_match")
                self.assertEqual(target.target_id, expected.id)
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, expected.rect)

    def test_filter_and_sort_target_id_accepts_common_toolbar_labels(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Filter results.", "Funnel", (120, 160, 100, 32)),
            ("Click funnel.", "Filter", (120, 160, 100, 32)),
            ("Sort ascending.", "A to Z", (120, 160, 100, 32)),
            ("Sort descending.", "Z to A", (120, 160, 100, 32)),
            ("Click A to Z.", "Sort ascending", (120, 160, 150, 32)),
        )
        for instruction, label, rect in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [ControlCandidate("c001", label, "button", rect)],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, rect)

    def test_filter_and_sort_text_match_overrides_wrong_geometry(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            (
                "Filter results.",
                ControlCandidate("c001", "Funnel", "button", (120, 160, 100, 32)),
                ControlCandidate("c002", "Search", "edit", (300, 160, 220, 32)),
            ),
            (
                "Sort ascending.",
                ControlCandidate("c001", "A to Z", "button", (120, 160, 100, 32)),
                ControlCandidate("c002", "Filter", "button", (300, 160, 100, 32)),
            ),
        )
        for instruction, expected, decoy in cases:
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target": {
                                "x": decoy.rect[0],
                                "y": decoy.rect[1],
                                "width": decoy.rect[2],
                                "height": decoy.rect[3],
                            },
                        }
                    ),
                    self._capture(),
                    [expected, decoy],
                )

                self.assertEqual(target.source, "text_match")
                self.assertEqual(target.target_id, expected.id)
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, expected.rect)

    def test_filter_reset_actions_reject_plain_filter_controls(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from help_session import resolve_help_target

        cases = (
            ("Clear filter.", "Filter"),
            ("Clear filter.", "Filter results"),
            ("Clear filter.", "Apply filter"),
            ("Reset filter.", "Filter"),
            ("Reset search.", "Search"),
            ("Reset query.", "Query"),
            ("Remove filter.", "Filter results"),
            ("Delete filter.", "Apply filter"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                candidate = ControlCandidate("c001", label, "button", (120, 160, 140, 32))
                target_id = resolve_candidate_target(
                    target_id="c001",
                    instruction=instruction,
                    candidates=[candidate],
                    model_rect=candidate.rect,
                )
                snap_target = snap_candidate_target(
                    instruction=instruction,
                    candidates=[candidate],
                    model_rect=candidate.rect,
                )
                help_target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                            "target": {
                                "x": candidate.rect[0],
                                "y": candidate.rect[1],
                                "width": candidate.rect[2],
                                "height": candidate.rect[3],
                            },
                        }
                    ),
                    self._capture(),
                    [candidate],
                )

                self.assertEqual(target_id.rejected_reason, "target_id semantic mismatch")
                self.assertIsNotNone(snap_target)
                assert snap_target is not None
                self.assertEqual(snap_target.rejected_reason, "candidate semantic mismatch")
                self.assertEqual(help_target.rejected_reason, "target_id semantic mismatch")

    def test_editor_toolbar_target_id_accepts_format_and_history_labels(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Bold text.", "B", (120, 160, 32, 32)),
            ("Click B.", "B", (120, 160, 32, 32)),
            ("Italic text.", "I", (120, 160, 32, 32)),
            ("Italicize selected text.", "I", (120, 160, 32, 32)),
            ("Underline text.", "U", (120, 160, 32, 32)),
            ("Undo change.", "\u21b6", (120, 160, 32, 32)),
            ("Redo change.", "\u21b7", (120, 160, 32, 32)),
            ("Undo change.", "Ctrl+Z", (120, 160, 90, 32)),
            ("Redo change.", "Ctrl+Shift+Z", (120, 160, 140, 32)),
            ("Remove formatting.", "Clear formatting", (120, 160, 150, 32)),
            ("Remove formatting.", "Remove formatting", (120, 160, 170, 32)),
        )
        for instruction, label, rect in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [ControlCandidate("c001", label, "button", rect)],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, rect)

    def test_editor_toolbar_text_match_overrides_wrong_geometry(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            (
                "Bold text.",
                ControlCandidate("c001", "B", "button", (120, 160, 32, 32)),
                ControlCandidate("c002", "Body text", "edit", (300, 160, 220, 32)),
            ),
            (
                "Italic text.",
                ControlCandidate("c001", "I", "button", (120, 160, 32, 32)),
                ControlCandidate("c002", "Body text", "edit", (300, 160, 220, 32)),
            ),
            (
                "Italicize selected text.",
                ControlCandidate("c001", "I", "button", (120, 160, 32, 32)),
                ControlCandidate("c002", "Body text", "edit", (300, 160, 220, 32)),
            ),
            (
                "Undo change.",
                ControlCandidate("c001", "\u21b6", "button", (120, 160, 32, 32)),
                ControlCandidate("c002", "Back", "button", (300, 160, 100, 32)),
            ),
            (
                "Redo change.",
                ControlCandidate("c001", "\u21b7", "button", (120, 160, 32, 32)),
                ControlCandidate("c002", "Next", "button", (300, 160, 100, 32)),
            ),
        )
        for instruction, expected, decoy in cases:
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target": {
                                "x": decoy.rect[0],
                                "y": decoy.rect[1],
                                "width": decoy.rect[2],
                                "height": decoy.rect[3],
                            },
                        }
                    ),
                    self._capture(),
                    [expected, decoy],
                )

                self.assertEqual(target.source, "text_match")
                self.assertEqual(target.target_id, expected.id)
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, expected.rect)

    def test_remove_formatting_rejects_delete_family_controls(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target
        from help_session import resolve_help_target

        for label in ("Delete", "Trash", "Remove"):
            with self.subTest(label=label):
                candidates = [
                    ControlCandidate("c001", label, "button", (120, 160, 120, 32)),
                ]
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": "Remove formatting.",
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    candidates,
                )
                text_target = resolve_candidate_target(
                    target_id="",
                    instruction="Remove formatting.",
                    candidates=candidates,
                )
                snap_target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": "Remove formatting.",
                            "target": {"x": 120, "y": 160, "width": 120, "height": 32},
                        }
                    ),
                    self._capture(),
                    candidates,
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, "target_id semantic mismatch")
                self.assertIsNone(text_target)
                self.assertEqual(snap_target.source, "candidate_snap")
                self.assertEqual(snap_target.target_id, "c001")
                self.assertEqual(snap_target.rejected_reason, "candidate semantic mismatch")

    def test_opposite_history_and_checkbox_actions_reject_shared_context(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target
        from help_session import resolve_help_target

        cases = (
            ("Undo change.", "Redo change", "button", (120, 160, 150, 32)),
            ("Redo change.", "Undo change", "button", (120, 160, 150, 32)),
            ("Enable notifications.", "Disable notifications", "checkbox", (120, 160, 190, 32)),
            ("Disable notifications.", "Enable notifications", "checkbox", (120, 160, 190, 32)),
            ("Enable notifications.", "Turn off notifications", "checkbox", (120, 160, 210, 32)),
            ("Turn on notifications.", "Disable notifications", "checkbox", (120, 160, 190, 32)),
            ("Activate notifications.", "Deactivate notifications", "button", (120, 160, 210, 32)),
            ("Deactivate notifications.", "Activate notifications", "button", (120, 160, 200, 32)),
            ("Check in guest.", "Check out guest", "button", (120, 160, 190, 32)),
            ("Check out guest.", "Check in guest", "button", (120, 160, 180, 32)),
            ("Check Remember me.", "Uncheck Remember me", "checkbox", (120, 160, 190, 32)),
            ("Uncheck Remember me.", "Check Remember me", "checkbox", (120, 160, 190, 32)),
        )
        for instruction, label, control_type, rect in cases:
            with self.subTest(instruction=instruction, label=label):
                candidates = [
                    ControlCandidate("c001", label, control_type, rect),
                ]
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    candidates,
                )
                text_target = resolve_candidate_target(
                    target_id="",
                    instruction=instruction,
                    candidates=candidates,
                )
                snap_target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target": {
                                "x": rect[0],
                                "y": rect[1],
                                "width": rect[2],
                                "height": rect[3],
                            },
                        }
                    ),
                    self._capture(),
                    candidates,
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, "target_id semantic mismatch")
                self.assertIsNone(text_target)
                self.assertEqual(snap_target.source, "candidate_snap")
                self.assertEqual(snap_target.target_id, "c001")
                self.assertEqual(snap_target.rejected_reason, "candidate semantic mismatch")

    def test_activate_account_recovers_from_active_status_label(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate("status", "Active account", "button", (100, 100, 140, 32)),
            ControlCandidate("activate", "", "button", (100, 150, 32, 32), automation_id="ActivateButton"),
        ]
        instruction = "Click Activate account."

        wrong_target = resolve_candidate_target(
            target_id="status",
            instruction=instruction,
            candidates=candidates,
            model_rect=(100, 100, 140, 32),
        )
        text_target = resolve_candidate_target(
            target_id="",
            instruction=instruction,
            candidates=candidates,
            model_rect=(100, 100, 140, 32),
        )
        snap_target = snap_candidate_target(
            instruction=instruction,
            candidates=candidates,
            model_rect=(100, 100, 140, 32),
        )
        help_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": instruction,
                    "target_id": "status",
                    "target": {"x": 100, "y": 100, "width": 140, "height": 32},
                }
            ),
            self._capture(),
            candidates,
        )

        self.assertEqual(wrong_target.target_id, "status")
        self.assertEqual(wrong_target.rejected_reason, "target_id semantic mismatch")
        self.assertEqual(text_target.target_id, "activate")
        self.assertFalse(text_target.rejected_reason)
        self.assertEqual(snap_target.target_id, "status")
        self.assertEqual(snap_target.rejected_reason, "candidate semantic mismatch")
        self.assertEqual(help_target.source, "text_match")
        self.assertEqual(help_target.target_id, "activate")
        self.assertFalse(help_target.rejected_reason)
        self.assertEqual(help_target.rect, (100, 150, 32, 32))

    def test_clear_and_delete_target_id_accepts_common_icon_labels(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            (
                "Clear search.",
                [
                    ControlCandidate("c001", "\u00d7", "button", (586, 166, 28, 28)),
                    ControlCandidate("c002", "Search", "edit", (120, 160, 500, 40)),
                ],
                (586, 166, 28, 28),
            ),
            (
                "Clear text.",
                [
                    ControlCandidate("c001", "X", "button", (586, 166, 28, 28)),
                    ControlCandidate("c002", "Body text", "edit", (120, 160, 500, 40)),
                ],
                (586, 166, 28, 28),
            ),
            (
                "Close dialog.",
                [ControlCandidate("c001", "X", "button", (120, 160, 32, 32))],
                (120, 160, 32, 32),
            ),
            (
                "Delete item.",
                [ControlCandidate("c001", "\U0001f5d1", "button", (120, 160, 32, 32))],
                (120, 160, 32, 32),
            ),
            (
                "Click wastebasket.",
                [ControlCandidate("c001", "Delete", "button", (120, 160, 100, 32))],
                (120, 160, 100, 32),
            ),
        )
        for instruction, candidates, rect in cases:
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    candidates,
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, rect)

    def test_clear_search_recovers_from_window_close_x_button(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate(
                "c001",
                "\u00d7",
                "button",
                (700, 20, 32, 32),
                automation_id="Close",
                window_title="Dialog",
            ),
            ControlCandidate("c002", "Clear", "button", (586, 166, 28, 28)),
            ControlCandidate("c003", "Search", "edit", (120, 160, 500, 40)),
        ]
        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Clear search.",
                    "target_id": "c001",
                }
            ),
            self._capture(),
            candidates,
        )
        text_target = resolve_candidate_target(
            target_id="",
            instruction="Clear search.",
            candidates=[candidates[0]],
        )
        snap_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Clear search.",
                    "target": {"x": 700, "y": 20, "width": 32, "height": 32},
                }
            ),
            self._capture(),
            candidates,
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c002")
        self.assertFalse(target.rejected_reason)
        self.assertIsNone(text_target)
        self.assertEqual(snap_target.source, "text_match")
        self.assertEqual(snap_target.target_id, "c002")
        self.assertFalse(snap_target.rejected_reason)

    def test_weather_widget_accepts_weather_and_widget_wording(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            "Open widgets.",
            "Open weather.",
            "Show weather.",
        )
        for instruction in cases:
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            "Widgets 64\u00b0F Clear",
                            "button",
                            (120, 160, 160, 32),
                            window_title="Taskbar",
                        )
                    ],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, (120, 160, 160, 32))

    def test_weather_widget_clear_status_does_not_cross_clear_actions(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Clear search.", "Widgets 64\u00b0F Clear"),
            ("Clear text.", "Widgets 64\u00b0F Clear"),
            ("Clear the field.", "Weather 64\u00b0F Clear"),
            ("Open weather.", "Clear"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            label,
                            "button",
                            (120, 160, 160, 32),
                            window_title="Taskbar",
                        )
                    ],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, "target_id semantic mismatch")

    def test_tab_search_and_windows_search_target_ids_do_not_cross(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            (
                "Search tabs.",
                "Search tabs",
                "button",
                "about:blank - Google Chrome",
                "",
            ),
            (
                "Open tab search.",
                "Search tabs",
                "button",
                "about:blank - Google Chrome",
                "",
            ),
            (
                "Search tabs.",
                "Search - World Reef Awareness Day",
                "button",
                "Taskbar",
                "target_id semantic mismatch",
            ),
            (
                "Open tab search.",
                "Search - World Reef Awareness Day",
                "button",
                "Taskbar",
                "target_id semantic mismatch",
            ),
            (
                "Open Windows search.",
                "Search - World Reef Awareness Day",
                "button",
                "Taskbar",
                "",
            ),
            (
                "Search Windows.",
                "Search - World Reef Awareness Day",
                "button",
                "Taskbar",
                "",
            ),
            (
                "Open Windows search.",
                "Search tabs",
                "button",
                "about:blank - Google Chrome",
                "target_id semantic mismatch",
            ),
        )
        for instruction, label, control_type, window_title, reason in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            label,
                            control_type,
                            (120, 160, 180, 32),
                            window_title=window_title,
                        )
                    ],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, reason)

    def test_generic_tabs_do_not_resolve_to_tab_search_button(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate(
                "c001",
                "Search tabs",
                "button",
                (120, 160, 100, 32),
                window_title="about:blank - Google Chrome",
            ),
            ControlCandidate(
                "c002",
                "about:blank",
                "tabitem",
                (240, 160, 220, 32),
                window_title="about:blank - Google Chrome",
            ),
        ]
        for instruction in ("Show tabs.", "Highlight tabs."):
            with self.subTest(instruction=instruction):
                text_target = resolve_candidate_target(
                    target_id="",
                    instruction=instruction,
                    candidates=candidates,
                    model_rect=None,
                )
                self.assertIsNone(text_target)

                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    candidates,
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(
                    target.rejected_reason,
                    "target_id control type mismatch",
                )

    def test_explicit_tab_search_text_match_accepts_search_tabs_button(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        candidates = [
            ControlCandidate(
                "c001",
                "Search tabs",
                "button",
                (120, 160, 100, 32),
                window_title="about:blank - Google Chrome",
            ),
            ControlCandidate(
                "c002",
                "about:blank",
                "tabitem",
                (240, 160, 220, 32),
                window_title="about:blank - Google Chrome",
            ),
        ]
        for instruction in ("Open tab search.", "Search tabs."):
            with self.subTest(instruction=instruction):
                target = resolve_candidate_target(
                    target_id="",
                    instruction=instruction,
                    candidates=candidates,
                    model_rect=None,
                )

                self.assertIsNotNone(target)
                assert target is not None
                self.assertEqual(target.source, "text_match")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)

    def test_generic_search_recovers_from_tab_search_to_windows_search(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate(
                "c001",
                "Search tabs",
                "button",
                (120, 160, 100, 32),
                window_title="about:blank - Google Chrome",
            ),
            ControlCandidate(
                "c002",
                "Search - World Reef Awareness Day",
                "button",
                (120, 740, 180, 32),
                window_title="Taskbar",
            ),
        ]

        text_target = resolve_candidate_target(
            target_id="",
            instruction="Open search.",
            candidates=candidates,
            model_rect=None,
        )
        self.assertIsNotNone(text_target)
        assert text_target is not None
        self.assertEqual(text_target.source, "text_match")
        self.assertEqual(text_target.target_id, "c002")
        self.assertFalse(text_target.rejected_reason)

        tab_search_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open search.",
                    "target_id": "c001",
                }
            ),
            self._capture(),
            candidates,
        )
        self.assertEqual(tab_search_target.source, "text_match")
        self.assertEqual(tab_search_target.target_id, "c002")
        self.assertFalse(tab_search_target.rejected_reason)

        windows_search_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open search.",
                    "target_id": "c002",
                }
            ),
            self._capture(),
            candidates,
        )
        self.assertEqual(windows_search_target.source, "target_id")
        self.assertEqual(windows_search_target.target_id, "c002")
        self.assertFalse(windows_search_target.rejected_reason)

    def test_generic_search_rejects_chrome_tab_search_button(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate(
                "c001",
                "Search tabs",
                "button",
                (120, 160, 100, 32),
                window_title="about:blank - Google Chrome",
            ),
        ]

        text_target = resolve_candidate_target(
            target_id="",
            instruction="Open search.",
            candidates=candidates,
            model_rect=None,
        )
        self.assertIsNone(text_target)

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open search.",
                    "target_id": "c001",
                }
            ),
            self._capture(),
            candidates,
        )

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.target_id, "c001")
        self.assertEqual(target.rejected_reason, "target_id semantic mismatch")

    def test_clear_and_delete_text_match_overrides_wrong_geometry(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            (
                "Clear search.",
                ControlCandidate("c001", "\u00d7", "button", (586, 166, 28, 28)),
                [
                    ControlCandidate("c002", "Search", "edit", (120, 160, 500, 40)),
                ],
                ControlCandidate("c003", "Cancel", "button", (300, 240, 100, 32)),
            ),
            (
                "Clear text.",
                ControlCandidate("c001", "X", "button", (586, 166, 28, 28)),
                [
                    ControlCandidate("c002", "Body text", "edit", (120, 160, 500, 40)),
                ],
                ControlCandidate("c003", "Cancel", "button", (300, 240, 100, 32)),
            ),
            (
                "Delete item.",
                ControlCandidate("c001", "\U0001f5d1", "button", (120, 160, 32, 32)),
                [],
                ControlCandidate("c002", "Cancel", "button", (300, 160, 100, 32)),
            ),
        )
        for instruction, expected, context_candidates, decoy in cases:
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target": {
                                "x": decoy.rect[0],
                                "y": decoy.rect[1],
                                "width": decoy.rect[2],
                                "height": decoy.rect[3],
                            },
                        }
                    ),
                    self._capture(),
                    [expected, *context_candidates, decoy],
                )

                self.assertEqual(target.source, "text_match")
                self.assertEqual(target.target_id, expected.id)
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, expected.rect)

    def test_delete_alias_rejects_ambiguous_delete_and_trash_actions(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Delete item.",
                    "target_id": "c001",
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Trash", "button", (120, 160, 100, 32)),
                ControlCandidate("c002", "Delete", "button", (280, 160, 100, 32)),
            ],
        )

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.target_id, "c001")
        self.assertEqual(target.rejected_reason, "target_id ambiguous")

    def test_zoom_target_id_accepts_directional_icon_labels(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Zoom in.", "+", (120, 160, 32, 32)),
            ("Zoom in.", "Plus", (120, 160, 70, 32)),
            ("Zoom out.", "-", (120, 160, 32, 32)),
            ("Zoom out.", "\u2212", (120, 160, 32, 32)),
            ("Zoom out.", "Minus", (120, 160, 80, 32)),
            ("Click minus.", "-", (120, 160, 32, 32)),
        )
        for instruction, label, rect in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [ControlCandidate("c001", label, "button", rect)],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, rect)

    def test_zoom_text_match_overrides_fit_geometry(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Zoom in.", ControlCandidate("c001", "+", "button", (120, 160, 32, 32))),
            ("Zoom out.", ControlCandidate("c001", "-", "button", (120, 160, 32, 32))),
            ("Zoom out.", ControlCandidate("c001", "\u2212", "button", (120, 160, 32, 32))),
        )
        for instruction, expected in cases:
            with self.subTest(instruction=instruction, label=expected.text):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target": {"x": 300, "y": 160, "width": 100, "height": 32},
                        }
                    ),
                    self._capture(),
                    [
                        expected,
                        ControlCandidate("c002", "Fit", "button", (300, 160, 100, 32)),
                    ],
                )

                self.assertEqual(target.source, "text_match")
                self.assertEqual(target.target_id, expected.id)
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, expected.rect)

    def test_zoom_alias_rejects_add_and_remove_actions(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Zoom in.", "Add"),
            ("Zoom out.", "Remove"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [ControlCandidate("c001", label, "button", (120, 160, 100, 32))],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, "target_id semantic mismatch")

    def test_window_control_target_id_accepts_common_caption_icons(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Minimize window.", "-", (120, 160, 32, 32)),
            ("Minimize window.", "\u2212", (120, 160, 32, 32)),
            ("Minimize window.", "\U0001f5d5", (120, 160, 32, 32)),
            ("Maximize window.", "\u25a1", (120, 160, 32, 32)),
            ("Maximize window.", "\u25a2", (120, 160, 32, 32)),
            ("Maximize window.", "\u2b1c", (120, 160, 32, 32)),
            ("Maximize window.", "\U0001f5d6", (120, 160, 32, 32)),
            ("Restore window.", "\U0001f5d7", (120, 160, 32, 32)),
            ("Close window.", "\u00d7", (120, 160, 32, 32)),
            ("Close window.", "\u2715", (120, 160, 32, 32)),
        )
        for instruction, label, rect in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [ControlCandidate("c001", label, "button", rect)],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, rect)

    def test_show_desktop_target_id_accepts_all_windows_wording(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            "Show desktop.",
            "Minimize all windows.",
            "Minimise all windows.",
            "Hide all windows.",
        )
        for instruction in cases:
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            "Show Desktop",
                            "button",
                            (120, 160, 120, 32),
                            window_title="Taskbar",
                        )
                    ],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, (120, 160, 120, 32))

    def test_show_desktop_aliases_do_not_cross_single_window_minimize(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Minimize all windows.", "Minimize", "about:blank - Google Chrome"),
            ("Hide all windows.", "Minimize", "about:blank - Google Chrome"),
            ("Minimize window.", "Show Desktop", "Taskbar"),
            ("Open desktop.", "Show Desktop", "Taskbar"),
            ("Click desktop.", "Show Desktop", "Taskbar"),
        )
        for instruction, label, window_title in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            label,
                            "button",
                            (120, 160, 120, 32),
                            window_title=window_title,
                        )
                    ],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, "target_id semantic mismatch")

    def test_bare_desktop_text_match_ignores_show_desktop(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="",
            instruction="Open desktop.",
            candidates=[
                ControlCandidate(
                    "c001",
                    "Show Desktop",
                    "button",
                    (120, 160, 12, 32),
                    window_title="Taskbar",
                )
            ],
        )

        self.assertIsNone(result)

    def test_bare_desktop_model_rect_rejects_show_desktop_snap(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open desktop.",
                    "target": {
                        "x": 120,
                        "y": 160,
                        "width": 12,
                        "height": 32,
                    },
                }
            ),
            self._capture(),
            [
                ControlCandidate(
                    "c001",
                    "Show Desktop",
                    "button",
                    (120, 160, 12, 32),
                    window_title="Taskbar",
                )
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.target_id, "c001")
        self.assertEqual(target.rejected_reason, "candidate semantic mismatch")

    def test_program_manager_generic_words_reject_desktop_icon_target_id(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Open desktop.", "Docker Desktop"),
            ("Click desktop.", "Docker Desktop"),
            ("Open about.", "Learn about this picture"),
            ("Show about.", "Learn about this picture"),
            ("Open new.", "New Pandora (1)"),
            ("Create new.", "New Pandora (1)"),
            ("Add new.", "New Pandora (1)"),
            ("Open app.", "SocialApp"),
            ("Open ai.", "Atlas.ai"),
            ("Open dev.", "Limitles.dev"),
            ("Open source.", "tweetpilot-source"),
            ("Open main.", "awesome-system-prompts-main"),
            ("Open system.", "awesome-system-prompts-main"),
            ("Open installer.", "MinecraftInstaller"),
            ("Open launcher.", "Rockstar Games Launcher"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            label,
                            "listitem",
                            (120, 160, 76, 54),
                            window_title="Program Manager",
                        )
                    ],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, "target_id semantic mismatch")

    def test_program_manager_distinctive_words_accept_desktop_icon_target_id(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Open Docker Desktop.", "Docker Desktop"),
            ("Open this picture.", "Learn about this picture"),
            ("Open learn about this picture.", "Learn about this picture"),
            ("Open Pandora.", "New Pandora (1)"),
            ("Open New Pandora.", "New Pandora (1)"),
            ("Open SocialApp.", "SocialApp"),
            ("Open Atlas.", "Atlas.ai"),
            ("Open Limitles.", "Limitles.dev"),
            ("Open tweetpilot source.", "tweetpilot-source"),
            ("Open awesome prompts.", "awesome-system-prompts-main"),
            ("Open Minecraft installer.", "MinecraftInstaller"),
            ("Open Rockstar launcher.", "Rockstar Games Launcher"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            label,
                            "listitem",
                            (120, 160, 76, 54),
                            window_title="Program Manager",
                        )
                    ],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)

    def test_program_manager_generic_words_ignore_desktop_icon_text_match(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        cases = (
            ("Open desktop.", "Docker Desktop"),
            ("Open about.", "Learn about this picture"),
            ("Open new.", "New Pandora (1)"),
            ("Open app.", "SocialApp"),
            ("Open ai.", "Atlas.ai"),
            ("Open dev.", "Limitles.dev"),
            ("Open source.", "tweetpilot-source"),
            ("Open main.", "awesome-system-prompts-main"),
            ("Open system.", "awesome-system-prompts-main"),
            ("Open installer.", "MinecraftInstaller"),
            ("Open launcher.", "Rockstar Games Launcher"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                result = resolve_candidate_target(
                    target_id="",
                    instruction=instruction,
                    candidates=[
                        ControlCandidate(
                            "c001",
                            label,
                            "listitem",
                            (120, 160, 76, 54),
                            window_title="Program Manager",
                        )
                    ],
                )

                self.assertIsNone(result)

    def test_program_manager_generic_words_reject_desktop_icon_snap(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Open desktop.", "Docker Desktop"),
            ("Open about.", "Learn about this picture"),
            ("Open new.", "New Pandora (1)"),
            ("Open app.", "SocialApp"),
            ("Open ai.", "Atlas.ai"),
            ("Open dev.", "Limitles.dev"),
            ("Open source.", "tweetpilot-source"),
            ("Open main.", "awesome-system-prompts-main"),
            ("Open system.", "awesome-system-prompts-main"),
            ("Open installer.", "MinecraftInstaller"),
            ("Open launcher.", "Rockstar Games Launcher"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target": {
                                "x": 120,
                                "y": 160,
                                "width": 76,
                                "height": 54,
                            },
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            label,
                            "listitem",
                            (120, 160, 76, 54),
                            window_title="Program Manager",
                        )
                    ],
                )

                self.assertEqual(target.source, "candidate_snap")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, "candidate semantic mismatch")

    def test_window_control_text_match_overrides_nearby_toolbar_geometry(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            (
                "Minimize window.",
                ControlCandidate("c001", "-", "button", (120, 160, 32, 32)),
                ControlCandidate("c002", "Zoom out", "button", (300, 160, 100, 32)),
            ),
            (
                "Maximize window.",
                ControlCandidate("c001", "\u25a1", "button", (120, 160, 32, 32)),
                ControlCandidate("c002", "Full screen", "button", (300, 160, 120, 32)),
            ),
            (
                "Restore window.",
                ControlCandidate("c001", "\U0001f5d7", "button", (120, 160, 32, 32)),
                ControlCandidate("c002", "Maximize", "button", (300, 160, 100, 32)),
            ),
            (
                "Close window.",
                ControlCandidate("c001", "\u00d7", "button", (120, 160, 32, 32)),
                ControlCandidate("c002", "Clear", "button", (300, 160, 100, 32)),
            ),
        )
        for instruction, expected, decoy in cases:
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target": {
                                "x": decoy.rect[0],
                                "y": decoy.rect[1],
                                "width": decoy.rect[2],
                                "height": decoy.rect[3],
                            },
                        }
                    ),
                    self._capture(),
                    [expected, decoy],
                )

                self.assertEqual(target.source, "text_match")
                self.assertEqual(target.target_id, expected.id)
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, expected.rect)

    def test_close_toolbar_target_id_rejects_window_close_button(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate(
                "c001",
                "Close",
                "button",
                (940, 10, 46, 40),
                window_title="MyApp - Google Chrome",
            ),
            ControlCandidate("c002", "Close toolbar", "button", (280, 80, 120, 32)),
        ]

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Close toolbar.",
                    "target_id": "c001",
                    "target": {"x": 940, "y": 10, "width": 46, "height": 40},
                }
            ),
            self._capture(),
            candidates,
        )
        snap = snap_candidate_target(
            instruction="Close toolbar.",
            candidates=[candidates[0]],
            model_rect=(940, 10, 46, 40),
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c002")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(snap.source, "candidate_snap")
        self.assertEqual(snap.rejected_reason, "candidate semantic mismatch")

    def test_close_tab_targets_tab_close_button_not_tabitem(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate(
                "c001",
                "Docs - Project Plan",
                "tabitem",
                (100, 0, 220, 40),
            ),
            ControlCandidate("c002", "Close", "button", (286, 8, 24, 24)),
        ]
        cases = (
            {
                "kind": "step",
                "instruction": "Close tab.",
                "target_id": "c002",
            },
            {
                "kind": "step",
                "instruction": "Close tab.",
                "target_id": "c001",
            },
            {
                "kind": "step",
                "instruction": "Close tab.",
                "target": {"x": 286, "y": 8, "width": 24, "height": 24},
            },
            {
                "kind": "step",
                "instruction": "Close tab.",
                "target": {"x": 100, "y": 0, "width": 220, "height": 40},
            },
        )
        for payload in cases:
            with self.subTest(payload=payload):
                target = resolve_help_target(
                    self._decision(payload),
                    self._capture(),
                    candidates,
                )

                self.assertIn(target.source, {"target_id", "text_match"})
                self.assertEqual(target.target_id, "c002")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, (286, 8, 24, 24))

    def test_close_tab_rejects_window_close_button_without_tab_context(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate(
                "c001",
                "Docs - Project Plan",
                "tabitem",
                (100, 0, 220, 40),
                window_title="about:blank - Google Chrome",
            ),
            ControlCandidate(
                "c002",
                "Close",
                "button",
                (900, 0, 46, 40),
                window_title="about:blank - Google Chrome",
            ),
        ]

        text_target = resolve_candidate_target(
            target_id="",
            instruction="Close tab.",
            candidates=candidates,
            model_rect=None,
        )
        self.assertIsNone(text_target)

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Close tab.",
                    "target_id": "c002",
                }
            ),
            self._capture(),
            candidates,
        )
        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.target_id, "c002")
        self.assertEqual(target.rejected_reason, "target_id semantic mismatch")

        snap = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Close tab.",
                    "target": {"x": 900, "y": 0, "width": 46, "height": 40},
                }
            ),
            self._capture(),
            candidates,
        )
        self.assertEqual(snap.source, "candidate_snap")
        self.assertEqual(snap.rejected_reason, "candidate snapshot no match")

    def test_close_page_prefers_browser_tab_close_over_window_close(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate(
                "tab",
                "Project plan",
                "tabitem",
                (100, 0, 220, 40),
                window_title="Project - Google Chrome",
            ),
            ControlCandidate(
                "tabclose",
                "Close",
                "button",
                (286, 8, 24, 24),
                window_title="Project - Google Chrome",
            ),
            ControlCandidate(
                "winclose",
                "Close",
                "button",
                (940, 0, 46, 40),
                window_title="Project - Google Chrome",
            ),
        ]

        for payload in (
            {
                "kind": "step",
                "instruction": "Close page.",
                "target_id": "winclose",
                "target": {"x": 940, "y": 0, "width": 46, "height": 40},
            },
            {
                "kind": "step",
                "instruction": "Close page.",
                "target": {"x": 940, "y": 0, "width": 46, "height": 40},
            },
        ):
            with self.subTest(payload=payload):
                target = resolve_help_target(
                    self._decision(payload),
                    self._capture(),
                    candidates,
                )

                self.assertEqual(target.target_id, "tabclose")
                self.assertEqual(target.rect, (286, 8, 24, 24))
                self.assertFalse(target.rejected_reason)

    def test_close_window_wrong_target_id_recovers_to_foreground_close(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Close window.",
                    "target_id": "c002",
                }
            ),
            self._capture(),
            [
                ControlCandidate(
                    "c001",
                    "Close",
                    "button",
                    (940, 10, 32, 32),
                    window_title="Foreground",
                    window_rank=0,
                ),
                ControlCandidate(
                    "c002",
                    "Close",
                    "button",
                    (940, 110, 32, 32),
                    window_title="Background",
                    window_rank=1,
                ),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (940, 10, 32, 32))

    def test_dialog_close_wrong_target_id_stays_ambiguous_with_duplicate_closes(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Close the dialog.",
                    "target_id": "c002",
                }
            ),
            self._capture(),
            [
                ControlCandidate(
                    "c001",
                    "Close",
                    "button",
                    (940, 10, 32, 32),
                    window_title="Foreground",
                    window_rank=0,
                ),
                ControlCandidate(
                    "c002",
                    "Close",
                    "button",
                    (940, 110, 32, 32),
                    window_title="Background",
                    window_rank=1,
                ),
            ],
        )

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.target_id, "c002")
        self.assertEqual(target.rejected_reason, "target_id ambiguous")

    def test_window_control_aliases_do_not_cross_zoom_controls(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Zoom out.", "Minimize"),
            ("Minimize window.", "Zoom out"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [ControlCandidate("c001", label, "button", (120, 160, 100, 32))],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, "target_id semantic mismatch")

    def test_navigation_arrow_target_id_accepts_icon_only_buttons(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Go back.", "\u2190"),
            ("Click Previous.", "\u2039"),
            ("Click Previous.", "<"),
            ("Click left arrow.", "\u2190"),
            ("Go forward.", "\u2192"),
            ("Click Continue.", "\u203a"),
            ("Click Continue.", ">"),
            ("Click right arrow.", "\u2192"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [ControlCandidate("c001", label, "button", (120, 160, 32, 32))],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, (120, 160, 32, 32))

    def test_navigation_arrow_text_match_overrides_history_geometry(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            (
                "Go back.",
                ControlCandidate("c001", "\u2190", "button", (120, 160, 32, 32)),
                ControlCandidate("c002", "Undo", "button", (300, 160, 100, 32)),
            ),
            (
                "Go forward.",
                ControlCandidate("c001", "\u2192", "button", (120, 160, 32, 32)),
                ControlCandidate("c002", "Redo", "button", (300, 160, 100, 32)),
            ),
        )
        for instruction, expected, decoy in cases:
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target": {
                                "x": decoy.rect[0],
                                "y": decoy.rect[1],
                                "width": decoy.rect[2],
                                "height": decoy.rect[3],
                            },
                        }
                    ),
                    self._capture(),
                    [expected, decoy],
                )

                self.assertEqual(target.source, "text_match")
                self.assertEqual(target.target_id, expected.id)
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, expected.rect)

    def test_browser_navigation_wording_rejects_page_local_back_button(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate(
                "page_back",
                "Back",
                "button",
                (80, 160, 96, 36),
                window_title="Docs - Google Chrome",
            ),
        ]

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click the browser Back button.",
                    "target_id": "page_back",
                }
            ),
            self._capture(),
            candidates,
        )

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.target_id, "page_back")
        self.assertEqual(target.rejected_reason, "target_id semantic mismatch")

        snap_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click the browser Back button.",
                    "target": {"x": 80, "y": 160, "width": 96, "height": 36},
                }
            ),
            self._capture(),
            candidates,
        )

        self.assertEqual(snap_target.source, "candidate_snap")
        self.assertEqual(snap_target.rejected_reason, "candidate snapshot no match")

        generic_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Back.",
                    "target_id": "page_back",
                }
            ),
            self._capture(),
            candidates,
        )

        self.assertEqual(generic_target.source, "target_id")
        self.assertEqual(generic_target.target_id, "page_back")
        self.assertFalse(generic_target.rejected_reason)

    def test_browser_navigation_wording_recovers_to_toolbar_back_button(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click the browser Back button.",
                    "target_id": "page_back",
                    "target": {"x": 80, "y": 160, "width": 96, "height": 36},
                }
            ),
            self._capture(),
            [
                ControlCandidate(
                    "browser_back",
                    "Back",
                    "button",
                    (16, 16, 32, 32),
                    automation_id="view_back",
                    window_title="Docs - Google Chrome",
                ),
                ControlCandidate(
                    "page_back",
                    "Back",
                    "button",
                    (80, 160, 96, 36),
                    window_title="Docs - Google Chrome",
                ),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "browser_back")
        self.assertEqual(target.rect, (16, 16, 32, 32))
        self.assertFalse(target.rejected_reason)

    def test_page_local_navigation_wording_recovers_from_browser_toolbar_back(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate(
                "browser_back",
                "Back",
                "button",
                (16, 50, 36, 32),
                window_title="Docs - Google Chrome",
            ),
            ControlCandidate(
                "page_back",
                "Back",
                "button",
                (80, 180, 80, 32),
                window_title="Docs - Google Chrome",
            ),
        ]
        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click the Back button on the page.",
                    "target_id": "browser_back",
                    "target": {"x": 16, "y": 50, "width": 36, "height": 32},
                }
            ),
            self._capture(),
            candidates,
        )
        snap_target = snap_candidate_target(
            instruction="Click the Back button on the page.",
            candidates=candidates,
            model_rect=(16, 50, 36, 32),
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "page_back")
        self.assertEqual(target.rect, (80, 180, 80, 32))
        self.assertFalse(target.rejected_reason)
        self.assertEqual(snap_target.source, "candidate_snap")
        self.assertEqual(snap_target.target_id, "browser_back")
        self.assertEqual(snap_target.rejected_reason, "candidate semantic mismatch")

    def test_page_local_find_wording_recovers_from_browser_toolbar_find(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate(
                "browser_find",
                "Find",
                "button",
                (760, 8, 80, 34),
                window_title="Docs - Google Chrome",
            ),
            ControlCandidate(
                "page_find",
                "Find",
                "button",
                (80, 220, 100, 36),
                window_title="Docs - Google Chrome",
            ),
        ]
        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Find on the page.",
                    "target_id": "browser_find",
                    "target": {"x": 760, "y": 8, "width": 80, "height": 34},
                }
            ),
            self._capture(),
            candidates,
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "page_find")
        self.assertEqual(target.rect, (80, 220, 100, 36))
        self.assertFalse(target.rejected_reason)

    def test_chrome_toolbar_reload_wording_recovers_from_page_reload(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate(
                "browser_reload",
                "Reload",
                "button",
                (16, 8, 34, 34),
                automation_id="reload",
                window_title="Docs - Google Chrome",
            ),
            ControlCandidate(
                "page_reload",
                "Reload",
                "button",
                (80, 220, 120, 36),
                window_title="Docs - Google Chrome",
            ),
        ]
        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click the Chrome toolbar Reload button.",
                    "target_id": "page_reload",
                    "target": {"x": 80, "y": 220, "width": 120, "height": 36},
                }
            ),
            self._capture(),
            candidates,
        )
        snap_target = snap_candidate_target(
            instruction="Click the Chrome toolbar Reload button.",
            candidates=candidates,
            model_rect=(80, 220, 120, 36),
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "browser_reload")
        self.assertEqual(target.rect, (16, 8, 34, 34))
        self.assertFalse(target.rejected_reason)
        self.assertIsNone(snap_target)

    def test_browser_page_share_wording_recovers_from_in_page_share(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate(
                "page_share",
                "Share",
                "button",
                (420, 180, 100, 32),
                window_title="Dashboard - Google Chrome",
            ),
            ControlCandidate(
                "chrome_share",
                "Share this page",
                "button",
                (900, 20, 90, 32),
                automation_id="share",
                window_title="Dashboard - Google Chrome",
            ),
        ]
        instruction = "Share browser page."

        wrong_target = resolve_candidate_target(
            target_id="page_share",
            instruction=instruction,
            candidates=candidates,
            model_rect=(420, 180, 100, 32),
        )
        text_target = resolve_candidate_target(
            target_id="",
            instruction=instruction,
            candidates=candidates,
            model_rect=(420, 180, 100, 32),
        )
        wrong_snap = snap_candidate_target(
            instruction=instruction,
            candidates=candidates,
            model_rect=(420, 180, 100, 32),
        )
        help_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": instruction,
                    "target_id": "page_share",
                    "target": {"x": 420, "y": 180, "width": 100, "height": 32},
                }
            ),
            self._capture(),
            candidates,
        )

        self.assertEqual(wrong_target.target_id, "page_share")
        self.assertEqual(wrong_target.rejected_reason, "target_id semantic mismatch")
        if wrong_snap is not None:
            self.assertEqual(wrong_snap.target_id, "page_share")
            self.assertTrue(wrong_snap.rejected_reason)
        for resolved in (text_target, help_target):
            self.assertEqual(resolved.target_id, "chrome_share")
            self.assertFalse(resolved.rejected_reason)
            self.assertEqual(resolved.rect, (900, 20, 90, 32))

    def test_navigation_arrow_aliases_do_not_cross_history_controls(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Undo last change.", "\u2190"),
            ("Go back.", "\u21b6"),
            ("Redo last change.", "\u2192"),
            ("Go forward.", "\u21b7"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [ControlCandidate("c001", label, "button", (120, 160, 32, 32))],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, "target_id semantic mismatch")

    def test_navigation_arrow_aliases_do_not_cross_media_transport_controls(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target
        from help_session import resolve_help_target

        cases = (
            ("Go forward.", "Next track"),
            ("Go forward.", "Next song"),
            ("Go back.", "Previous track"),
            ("Go back.", "Previous song"),
            ("Go back.", "Back up and sync"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                candidates = [
                    ControlCandidate("c001", label, "button", (120, 160, 140, 32)),
                ]
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    candidates,
                )
                text_target = resolve_candidate_target(
                    target_id="",
                    instruction=instruction,
                    candidates=candidates,
                )
                snap_target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target": {"x": 120, "y": 160, "width": 140, "height": 32},
                        }
                    ),
                    self._capture(),
                    candidates,
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, "target_id semantic mismatch")
                self.assertIsNone(text_target)
                self.assertEqual(snap_target.source, "candidate_snap")
                self.assertEqual(snap_target.target_id, "c001")
                self.assertEqual(snap_target.rejected_reason, "candidate semantic mismatch")

    def test_media_transport_instruction_still_accepts_next_previous_track(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Next track.", "Next track"),
            ("Previous song.", "Previous song"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [ControlCandidate("c001", label, "button", (120, 160, 140, 32))],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)

    def test_disclosure_arrow_target_id_accepts_icon_only_buttons(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Click the chevron.", "\u203a"),
            ("Click the down arrow.", "\u25be"),
            ("Expand Advanced settings.", "\u25b8"),
            ("Collapse Advanced settings.", "\u2304"),
            ("Collapse Advanced settings.", "\u25be"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [ControlCandidate("c001", label, "button", (120, 160, 32, 32))],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, (120, 160, 32, 32))

    def test_disclosure_state_target_id_rejects_opposite_action(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Expand Advanced settings.", "Collapse Advanced settings"),
            ("Collapse Advanced settings.", "Expand Advanced settings"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [ControlCandidate("c001", label, "button", (120, 160, 220, 32))],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, "target_id semantic mismatch")

    def test_disclosure_state_text_match_uses_matching_action(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Expand Advanced settings.",
                    "target": {"x": 120, "y": 160, "width": 220, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate(
                    "c001",
                    "Collapse Advanced settings",
                    "button",
                    (120, 160, 220, 32),
                ),
                ControlCandidate(
                    "c002",
                    "Expand Advanced settings",
                    "button",
                    (120, 220, 220, 32),
                ),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c002")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (120, 220, 220, 32))

    def test_disclosure_state_candidate_snap_rejects_opposite_action(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Expand Advanced settings.",
                    "target": {"x": 120, "y": 160, "width": 220, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate(
                    "c001",
                    "Collapse Advanced settings",
                    "button",
                    (120, 160, 220, 32),
                )
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.target_id, "c001")
        self.assertEqual(target.rejected_reason, "candidate semantic mismatch")

    def test_disclosure_arrow_text_match_overrides_broad_row_geometry(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Click the chevron.", "\u203a"),
            ("Expand Advanced settings.", "\u25b8"),
            ("Collapse Advanced settings.", "\u25be"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target": {"x": 120, "y": 160, "width": 500, "height": 80},
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            "Advanced settings",
                            "listitem",
                            (120, 160, 500, 80),
                        ),
                        ControlCandidate("c002", label, "button", (578, 186, 28, 28)),
                    ],
                )

                self.assertEqual(target.source, "text_match")
                self.assertEqual(target.target_id, "c002")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, (578, 186, 28, 28))

    def test_transfer_and_refresh_alias_target_id_accepts_matching_action(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Download the report.", "Export"),
            ("Export the report.", "Download"),
            ("Import data.", "Upload"),
            ("Upload data.", "Import"),
            ("Refresh the page.", "Reload"),
            ("Reload the page.", "Refresh"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [ControlCandidate("c001", label, "button", (120, 160, 120, 32))],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, (120, 160, 120, 32))

    def test_refresh_target_id_accepts_icon_only_buttons(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Refresh the page.", "\u27f2"),
            ("Refresh the page.", "\u27f3"),
            ("Reload the page.", "\U0001f503"),
            ("Reload the page.", "\U0001f504"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [ControlCandidate("c001", label, "button", (120, 160, 32, 32))],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, (120, 160, 32, 32))

    def test_refresh_icon_text_match_overrides_navigation_geometry(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Refresh the page.",
                    "target": {"x": 300, "y": 160, "width": 100, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "\u27f3", "button", (120, 160, 32, 32)),
                ControlCandidate("c002", "Back", "button", (300, 160, 100, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (120, 160, 32, 32))

    def test_refresh_icon_aliases_do_not_cross_history_controls(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Refresh the page.", "\u21bb"),
            ("Redo last change.", "\u27f3"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [ControlCandidate("c001", label, "button", (120, 160, 32, 32))],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, "target_id semantic mismatch")

    def test_download_action_alias_text_match_overrides_wrong_geometry(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Download the report.",
                    "target": {"x": 300, "y": 160, "width": 100, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Export", "button", (120, 160, 120, 32)),
                ControlCandidate("c002", "Cancel", "button", (300, 160, 100, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (120, 160, 120, 32))

    def test_transfer_alias_rejects_ambiguous_download_and_export_actions(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Download the report.",
                    "target_id": "c001",
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Export", "button", (120, 160, 120, 32)),
                ControlCandidate("c002", "Download", "button", (280, 160, 140, 32)),
            ],
        )

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.target_id, "c001")
        self.assertEqual(target.rejected_reason, "target_id ambiguous")

    def test_transfer_alias_ambiguity_survives_stale_geometry_snap(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate("c001", "Export", "button", (120, 160, 120, 32)),
            ControlCandidate("c002", "Download", "button", (280, 160, 140, 32)),
            ControlCandidate("c003", "Cancel", "button", (500, 160, 100, 32)),
        ]
        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Download the report.",
                    "target_id": "c003",
                    "target": {"x": 120, "y": 160, "width": 120, "height": 32},
                }
            ),
            self._capture(),
            candidates,
        )
        snap = snap_candidate_target(
            instruction="Download the report.",
            candidates=candidates,
            model_rect=(120, 160, 120, 32),
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertEqual(target.rejected_reason, "ambiguous text match")
        self.assertEqual(snap.source, "candidate_snap")
        self.assertEqual(snap.target_id, "c001")
        self.assertEqual(snap.rejected_reason, "candidate semantic mismatch")

    def test_share_and_archive_target_id_accepts_common_icon_labels(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Share this item.", "\U0001f517", (120, 160, 32, 32)),
            ("Archive item.", "\U0001f5c4", (120, 160, 32, 32)),
            ("Archive item.", "File cabinet", (120, 160, 120, 32)),
        )
        for instruction, label, rect in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [ControlCandidate("c001", label, "button", rect)],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, rect)

    def test_external_link_target_id_accepts_open_new_icons_and_labels(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Open external link.", "External link", (120, 160, 120, 32)),
            ("Open in new tab.", "\u2197", (120, 160, 32, 32)),
            ("Open in new tab.", "New tab", (120, 160, 100, 32)),
            ("Open in new window.", "\u29c9", (120, 160, 32, 32)),
            ("Launch item.", "External link", (120, 160, 120, 32)),
        )
        for instruction, label, rect in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [ControlCandidate("c001", label, "button", rect)],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, rect)

    def test_generic_new_rejects_browser_new_tab_button(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target
        from help_session import resolve_help_target

        for window_title in ("GitHub - Google Chrome", "Vidbox - Brave"):
            candidates = [
                ControlCandidate(
                    "c001",
                    "New Tab",
                    "button",
                    (120, 160, 32, 32),
                    window_title=window_title,
                )
            ]
            for instruction in ("Open new.", "Create new.", "Add new."):
                with self.subTest(instruction=instruction, window_title=window_title):
                    target = resolve_help_target(
                        self._decision(
                            {
                                "kind": "step",
                                "instruction": instruction,
                                "target_id": "c001",
                            }
                        ),
                        self._capture(),
                        candidates,
                    )
                    text_target = resolve_candidate_target(
                        target_id="",
                        instruction=instruction,
                        candidates=candidates,
                    )
                    snap_target = resolve_help_target(
                        self._decision(
                            {
                                "kind": "step",
                                "instruction": instruction,
                                "target": {"x": 120, "y": 160, "width": 32, "height": 32},
                            }
                        ),
                        self._capture(),
                        candidates,
                    )

                    self.assertEqual(target.source, "target_id")
                    self.assertEqual(target.target_id, "c001")
                    self.assertEqual(target.rejected_reason, "target_id semantic mismatch")
                    self.assertIsNone(text_target)
                    self.assertEqual(snap_target.source, "candidate_snap")
                    self.assertEqual(snap_target.target_id, "c001")
                    self.assertEqual(snap_target.rejected_reason, "candidate semantic mismatch")

    def test_external_and_new_window_wording_rejects_browser_new_tab_button(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate(
                "c001",
                "New Tab",
                "button",
                (120, 160, 100, 32),
                window_title="ChatGPT - Google Chrome",
            )
        ]
        cases = (
            "Open external.",
            "Open external link.",
            "Open in new window.",
        )
        for instruction in cases:
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    candidates,
                )
                text_target = resolve_candidate_target(
                    target_id="",
                    instruction=instruction,
                    candidates=candidates,
                )
                snap_target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target": {"x": 120, "y": 160, "width": 100, "height": 32},
                        }
                    ),
                    self._capture(),
                    candidates,
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, "target_id semantic mismatch")
                self.assertIsNone(text_target)
                self.assertEqual(snap_target.source, "candidate_snap")
                self.assertEqual(snap_target.target_id, "c001")
                self.assertEqual(snap_target.rejected_reason, "candidate semantic mismatch")

    def test_brave_site_information_requires_info_or_lock_wording(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Open view.", "target_id semantic mismatch"),
            ("Click view.", "target_id semantic mismatch"),
            ("Open site information.", ""),
            ("Click the site info button.", ""),
        )
        for instruction, reason in cases:
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            "View site information",
                            "button",
                            (120, 160, 160, 32),
                            window_title="Vidbox - Brave",
                        )
                    ],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, reason)

    def test_new_tab_wording_accepts_browser_new_tab_button(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = ("Open new tab.", "Open in new tab.")
        for window_title in ("GitHub - Google Chrome", "Vidbox - Brave"):
            for instruction in cases:
                with self.subTest(instruction=instruction, window_title=window_title):
                    target = resolve_help_target(
                        self._decision(
                            {
                                "kind": "step",
                                "instruction": instruction,
                                "target_id": "c001",
                            }
                        ),
                        self._capture(),
                        [
                            ControlCandidate(
                                "c001",
                                "New Tab",
                                "button",
                                (120, 160, 32, 32),
                                window_title=window_title,
                            )
                        ],
                    )

                    self.assertEqual(target.source, "target_id")
                    self.assertEqual(target.target_id, "c001")
                    self.assertFalse(target.rejected_reason)

    def test_external_link_aliases_do_not_cross_share_link_icons(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        share_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Share link.",
                    "target_id": "c001",
                }
            ),
            self._capture(),
            [ControlCandidate("c001", "\u2197", "button", (120, 160, 32, 32))],
        )
        external_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open external link.",
                    "target_id": "c001",
                }
            ),
            self._capture(),
            [ControlCandidate("c001", "\U0001f517", "button", (120, 160, 32, 32))],
        )

        self.assertEqual(share_target.source, "target_id")
        self.assertEqual(share_target.rejected_reason, "target_id control type mismatch")
        self.assertEqual(external_target.source, "target_id")
        self.assertEqual(external_target.rejected_reason, "target_id semantic mismatch")

    def test_share_and_archive_text_match_overrides_export_geometry(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            (
                "Share this item.",
                ControlCandidate("c001", "\U0001f517", "button", (120, 160, 32, 32)),
            ),
            (
                "Archive item.",
                ControlCandidate("c001", "\U0001f5c4", "button", (120, 160, 32, 32)),
            ),
        )
        for instruction, expected in cases:
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target": {"x": 300, "y": 160, "width": 100, "height": 32},
                        }
                    ),
                    self._capture(),
                    [
                        expected,
                        ControlCandidate("c002", "Export", "button", (300, 160, 100, 32)),
                    ],
                )

                self.assertEqual(target.source, "text_match")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, (120, 160, 32, 32))

    def test_send_action_alias_target_id_accepts_submit_button(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Send the message.", "Submit"),
            ("Submit the form.", "Send"),
            ("Send the message.", "Paper plane"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [ControlCandidate("c001", label, "button", (120, 160, 100, 32))],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, (120, 160, 100, 32))

    def test_send_action_alias_text_match_overrides_wrong_geometry(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Send the message.", "Submit"),
            ("Submit the form.", "Send"),
            ("Send message.", "Paper plane"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target": {"x": 300, "y": 160, "width": 100, "height": 32},
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate("c001", label, "button", (120, 160, 100, 32)),
                        ControlCandidate("c002", "Cancel", "button", (300, 160, 100, 32)),
                    ],
                )

                self.assertEqual(target.source, "text_match")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, (120, 160, 100, 32))

    def test_send_action_alias_text_match_overrides_message_field_geometry(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Send message.",
                    "target": {"x": 300, "y": 160, "width": 220, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Paper plane", "button", (120, 160, 100, 32)),
                ControlCandidate("c002", "Message", "edit", (300, 160, 220, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (120, 160, 100, 32))

    def test_send_alias_prefers_exact_send_over_submit_alias(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Send the message.",
                    "target_id": "c001",
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Submit", "button", (120, 160, 100, 32)),
                ControlCandidate("c002", "Send", "button", (280, 160, 100, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c002")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (280, 160, 100, 32))

    def test_action_family_mismatch_rejects_generic_overlap_button(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target
        from help_session import resolve_help_target

        cases = (
            ("Click Send message.", "Delete message"),
            ("Delete message.", "Send message"),
            ("Save document.", "Delete document"),
            ("Download file.", "Upload file"),
            ("Accept invite.", "Decline invite"),
            ("Decline invite.", "Accept invite"),
            ("Allow access.", "Deny access"),
            ("Deny access.", "Allow access"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                candidates = [
                    ControlCandidate("c001", label, "button", (120, 160, 180, 32)),
                ]
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    candidates,
                )
                text_target = resolve_candidate_target(
                    target_id="",
                    instruction=instruction,
                    candidates=candidates,
                )
                snap_target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target": {"x": 120, "y": 160, "width": 180, "height": 32},
                        }
                    ),
                    self._capture(),
                    candidates,
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, "target_id semantic mismatch")
                self.assertIsNone(text_target)
                self.assertEqual(snap_target.source, "candidate_snap")
                self.assertEqual(snap_target.target_id, "c001")
                self.assertEqual(snap_target.rejected_reason, "candidate semantic mismatch")

    def test_meeting_control_alias_target_id_accepts_common_labels(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Mute microphone.", "Mic", (120, 160, 80, 32)),
            ("Mute mic.", "Microphone", (120, 160, 120, 32)),
            ("Mute audio.", "Speaker", (120, 160, 100, 32)),
            ("Mute speaker.", "Sound", (120, 160, 90, 32)),
            ("Open volume.", "Speaker", (120, 160, 100, 32)),
            ("Increase volume.", "Volume", (120, 160, 100, 32)),
            ("Lower volume.", "Speaker", (120, 160, 100, 32)),
            ("Start video.", "Camera", (120, 160, 100, 32)),
            ("Start camera.", "Video", (120, 160, 90, 32)),
            ("Start webcam.", "Camera", (120, 160, 100, 32)),
            ("Mute microphone.", "\U0001f3a4", (120, 160, 32, 32)),
            ("Mute audio.", "\U0001f50a", (120, 160, 32, 32)),
            ("Mute audio.", "\U0001f507", (120, 160, 32, 32)),
            ("Start video.", "\U0001f4f7", (120, 160, 32, 32)),
        )
        for instruction, label, rect in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [ControlCandidate("c001", label, "button", rect)],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, rect)

    def test_audio_volume_direction_rejects_opposite_button(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target
        from help_session import resolve_help_target

        cases = (
            ("Increase volume.", "Decrease volume"),
            ("Decrease volume.", "Increase volume"),
            ("Volume up.", "Volume down"),
            ("Lower volume.", "Raise volume"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                candidates = [
                    ControlCandidate("c001", label, "button", (360, 160, 140, 32)),
                ]
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    candidates,
                )
                text_target = resolve_candidate_target(
                    target_id="",
                    instruction=instruction,
                    candidates=candidates,
                )
                snap_target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target": {"x": 360, "y": 160, "width": 140, "height": 32},
                        }
                    ),
                    self._capture(),
                    candidates,
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, "target_id semantic mismatch")
                self.assertIsNone(text_target)
                self.assertEqual(snap_target.source, "candidate_snap")
                self.assertEqual(snap_target.target_id, "c001")
                self.assertEqual(snap_target.rejected_reason, "candidate semantic mismatch")

    def test_start_video_rejects_taskbar_start_button(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Start video.", "target_id semantic mismatch"),
            ("Start camera.", "target_id semantic mismatch"),
            ("Click Start.", ""),
            ("Open Start button.", ""),
            ("Open Start menu.", ""),
            ("Open menu.", "target_id semantic mismatch"),
        )
        for instruction, reason in cases:
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            "Start",
                            "button",
                            (120, 160, 55, 40),
                            automation_id="StartButton",
                            window_title="Taskbar",
                        )
                    ],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, reason)

    def test_start_video_model_rect_rejects_taskbar_start_button_snap(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Start video.",
                    "target": {"x": 120, "y": 160, "width": 55, "height": 40},
                }
            ),
            self._capture(),
            [
                ControlCandidate(
                    "c001",
                    "Start",
                    "button",
                    (120, 160, 55, 40),
                    automation_id="StartButton",
                    window_title="Taskbar",
                )
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.target_id, "c001")
        self.assertEqual(target.rejected_reason, "candidate semantic mismatch")

    def test_meeting_control_alias_text_match_overrides_settings_geometry(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            (
                "Mute microphone.",
                ControlCandidate("c001", "Mic", "button", (120, 160, 80, 32)),
                ControlCandidate("c002", "Audio settings", "button", (300, 160, 140, 32)),
            ),
            (
                "Mute audio.",
                ControlCandidate("c001", "Speaker", "button", (120, 160, 100, 32)),
                ControlCandidate("c002", "Audio settings", "button", (300, 160, 140, 32)),
            ),
            (
                "Start video.",
                ControlCandidate("c001", "Camera", "button", (120, 160, 100, 32)),
                ControlCandidate("c002", "AV settings", "button", (300, 160, 150, 32)),
            ),
        )
        for instruction, expected, decoy in cases:
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target": {
                                "x": decoy.rect[0],
                                "y": decoy.rect[1],
                                "width": decoy.rect[2],
                                "height": decoy.rect[3],
                            },
                        }
                    ),
                    self._capture(),
                    [expected, decoy],
                )

                self.assertEqual(target.source, "text_match")
                self.assertEqual(target.target_id, expected.id)
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, expected.rect)

    def test_meeting_control_alias_rejects_ambiguous_exact_and_alias_actions(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            (
                "Mute microphone.",
                ControlCandidate("c001", "Mic", "button", (120, 160, 80, 32)),
                ControlCandidate("c002", "Microphone", "button", (240, 160, 120, 32)),
            ),
            (
                "Mute speaker.",
                ControlCandidate("c001", "Speaker", "button", (120, 160, 100, 32)),
                ControlCandidate("c002", "Sound", "button", (240, 160, 90, 32)),
            ),
            (
                "Start video.",
                ControlCandidate("c001", "Camera", "button", (120, 160, 100, 32)),
                ControlCandidate("c002", "Video", "button", (240, 160, 90, 32)),
            ),
        )
        for instruction, target_candidate, competing_candidate in cases:
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [target_candidate, competing_candidate],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, "target_id ambiguous")

    def test_media_control_symbol_target_id_accepts_common_icons(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Play video.", "\u25b6", (120, 160, 32, 32)),
            ("Pause video.", "\u23f8", (120, 160, 32, 32)),
            ("Stop playback.", "\u23f9", (120, 160, 32, 32)),
            ("Record clip.", "\u23fa", (120, 160, 32, 32)),
            ("Resume playback.", "Play", (120, 160, 80, 32)),
        )
        for instruction, label, rect in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [ControlCandidate("c001", label, "button", rect)],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, rect)

    def test_media_action_text_match_overrides_camera_and_settings_geometry(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            (
                "Play video.",
                ControlCandidate("c001", "\u25b6", "button", (120, 160, 32, 32)),
                ControlCandidate("c002", "Video settings", "button", (300, 160, 160, 32)),
            ),
            (
                "Pause video.",
                ControlCandidate("c001", "Pause", "button", (120, 160, 90, 32)),
                ControlCandidate("c002", "Camera", "button", (300, 160, 100, 32)),
            ),
        )
        for instruction, expected, decoy in cases:
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target": {
                                "x": decoy.rect[0],
                                "y": decoy.rect[1],
                                "width": decoy.rect[2],
                                "height": decoy.rect[3],
                            },
                        }
                    ),
                    self._capture(),
                    [expected, decoy],
                )

                self.assertEqual(target.source, "text_match")
                self.assertEqual(target.target_id, expected.id)
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, expected.rect)

    def test_edit_action_target_id_accepts_common_button_labels(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Edit this row.", "Edit", (120, 160, 80, 32)),
            ("Edit this row.", "Pencil", (120, 160, 90, 32)),
            ("Edit this row.", "\u270f", (120, 160, 32, 32)),
            ("Edit profile.", "Pencil", (120, 160, 90, 32)),
        )
        for instruction, label, rect in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [ControlCandidate("c001", label, "button", rect)],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, rect)

    def test_edit_action_text_match_overrides_edit_field_geometry(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Edit this row.",
                    "target": {"x": 300, "y": 160, "width": 220, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Pencil", "button", (120, 160, 90, 32)),
                ControlCandidate("c002", "Name", "edit", (300, 160, 220, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (120, 160, 90, 32))

    def test_literal_edit_control_instruction_still_targets_edit_field(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click this edit control.",
                    "target_id": "c001",
                    "target": {"x": 300, "y": 160, "width": 220, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Name", "edit", (300, 160, 220, 32)),
                ControlCandidate("c002", "Edit", "button", (120, 160, 80, 32)),
            ],
        )

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (300, 160, 220, 32))

    def test_audio_settings_instruction_targets_settings_not_speaker_button(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open audio settings.",
                    "target_id": "c001",
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Audio settings", "button", (300, 160, 140, 32)),
                ControlCandidate("c002", "Speaker", "button", (120, 160, 100, 32)),
            ],
        )

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (300, 160, 140, 32))

    def test_audio_settings_rejects_taskbar_volume_status_snap(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from help_session import resolve_help_target

        candidate = ControlCandidate(
            "volume",
            "Volume Speakers (Realtek(R) Audio): 24%",
            "button",
            (760, 960, 160, 32),
            automation_id="SystemTrayIcon",
            window_title="Taskbar",
        )
        target_id = resolve_candidate_target(
            target_id="volume",
            instruction="Open audio settings.",
            candidates=[candidate],
            model_rect=candidate.rect,
        )
        snap_target = snap_candidate_target(
            instruction="Open audio settings.",
            candidates=[candidate],
            model_rect=candidate.rect,
        )
        help_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open audio settings.",
                    "target_id": "volume",
                    "target": {
                        "x": candidate.rect[0],
                        "y": candidate.rect[1],
                        "width": candidate.rect[2],
                        "height": candidate.rect[3],
                    },
                }
            ),
            self._capture(),
            [candidate],
        )

        self.assertEqual(target_id.rejected_reason, "target_id semantic mismatch")
        self.assertIsNone(snap_target)
        self.assertEqual(help_target.rejected_reason, "target_id semantic mismatch")

    def test_selected_text_actions_recover_to_toolbar_buttons(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Delete selected text.", "Delete"),
            ("Share selected text.", "Share"),
            ("Print selected text.", "Print"),
            ("Save selected text.", "Save"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target": {"x": 300, "y": 160, "width": 220, "height": 32},
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate("c001", label, "button", (120, 160, 100, 32)),
                        ControlCandidate("c002", "Body text", "edit", (300, 160, 220, 32)),
                    ],
                )

                self.assertEqual(target.source, "text_match")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, (120, 160, 100, 32))

    def test_selected_text_actions_reject_page_action_duplicates(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from help_session import resolve_help_target

        cases = (
            (
                "Share selected text.",
                ControlCandidate("editor_share", "Share", "button", (120, 160, 80, 32)),
                ControlCandidate(
                    "browser_share",
                    "Share this page",
                    "button",
                    (760, 8, 42, 34),
                    automation_id="share",
                    window_title="Chrome",
                ),
            ),
            (
                "Print selected text.",
                ControlCandidate("editor_print", "Print", "button", (120, 160, 80, 32)),
                ControlCandidate("page_print", "Print page", "button", (760, 8, 80, 34)),
            ),
            (
                "Save selected text.",
                ControlCandidate("editor_save", "Save", "button", (120, 160, 80, 32)),
                ControlCandidate("page_save", "Save page", "button", (760, 8, 80, 34)),
            ),
            (
                "Share selected text.",
                ControlCandidate("editor_share", "Share", "button", (120, 160, 80, 32)),
                ControlCandidate(
                    "chrome_share",
                    "Share",
                    "button",
                    (760, 8, 42, 34),
                    automation_id="share",
                    window_title="Chrome",
                ),
            ),
            (
                "Print selected text.",
                ControlCandidate("editor_print", "Print", "button", (120, 160, 80, 32)),
                ControlCandidate(
                    "chrome_print",
                    "Print",
                    "button",
                    (760, 8, 42, 34),
                    automation_id="print",
                    window_title="Chrome",
                ),
            ),
            (
                "Save selected text.",
                ControlCandidate("editor_save", "Save", "button", (120, 160, 80, 32)),
                ControlCandidate(
                    "chrome_save",
                    "Save",
                    "button",
                    (760, 8, 42, 34),
                    automation_id="save",
                    window_title="Chrome",
                ),
            ),
            (
                "Copy selected text.",
                ControlCandidate("editor_copy", "Copy", "button", (120, 160, 80, 32)),
                ControlCandidate(
                    "chrome_copy",
                    "Copy",
                    "button",
                    (760, 8, 42, 34),
                    automation_id="copy",
                    window_title="Chrome",
                ),
            ),
        )
        for instruction, expected, wrong in cases:
            with self.subTest(instruction=instruction):
                candidates = [
                    expected,
                    wrong,
                    ControlCandidate("body", "Body text", "edit", (220, 210, 460, 260)),
                ]

                target_id = resolve_candidate_target(
                    target_id=wrong.id,
                    instruction=instruction,
                    candidates=candidates,
                    model_rect=wrong.rect,
                )
                snap_target = snap_candidate_target(
                    instruction=instruction,
                    candidates=candidates,
                    model_rect=wrong.rect,
                )
                help_target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": wrong.id,
                            "target": {
                                "x": wrong.rect[0],
                                "y": wrong.rect[1],
                                "width": wrong.rect[2],
                                "height": wrong.rect[3],
                            },
                        }
                    ),
                    self._capture(),
                    candidates,
                )

                self.assertEqual(target_id.target_id, wrong.id)
                self.assertEqual(target_id.rejected_reason, "target_id semantic mismatch")
                self.assertEqual(snap_target.target_id, wrong.id)
                self.assertEqual(snap_target.rejected_reason, "candidate semantic mismatch")
                self.assertEqual(help_target.source, "text_match")
                self.assertEqual(help_target.target_id, expected.id)
                self.assertFalse(help_target.rejected_reason)
                self.assertEqual(help_target.rect, expected.rect)

    def test_file_action_requests_reject_object_only_file_button(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target
        from help_session import resolve_help_target

        cases = (
            ("Save file.", "Save"),
            ("Download file.", "Download"),
            ("Attach file.", "Attach"),
        )
        for instruction, action_label in cases:
            with self.subTest(instruction=instruction):
                candidates = [
                    ControlCandidate("c001", "File", "button", (120, 160, 150, 32)),
                    ControlCandidate("c002", action_label, "button", (320, 160, 170, 32)),
                ]
                target_id = resolve_candidate_target(
                    target_id="c001",
                    instruction=instruction,
                    candidates=candidates,
                    model_rect=candidates[0].rect,
                )
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                            "target": {
                                "x": candidates[0].rect[0],
                                "y": candidates[0].rect[1],
                                "width": candidates[0].rect[2],
                                "height": candidates[0].rect[3],
                            },
                        }
                    ),
                    self._capture(),
                    candidates,
                )

                self.assertEqual(target_id.rejected_reason, "target_id semantic mismatch")
                self.assertEqual(target.source, "text_match")
                self.assertEqual(target.target_id, "c002")
                self.assertFalse(target.rejected_reason)

    def test_selected_file_actions_prefer_exact_action_over_alias_neighbor(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Download selected file.", "Upload", "Download"),
            ("Attach selected file.", "Upload", "Attach"),
            ("Upload selected file.", "Attach", "Upload"),
        )
        for instruction, decoy_label, action_label in cases:
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                            "target": {"x": 120, "y": 160, "width": 150, "height": 32},
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate("c001", decoy_label, "button", (120, 160, 150, 32)),
                        ControlCandidate("c002", action_label, "button", (320, 160, 170, 32)),
                    ],
                )

                self.assertEqual(target.source, "text_match")
                self.assertEqual(target.target_id, "c002")
                self.assertFalse(target.rejected_reason)

    def test_security_control_alias_target_id_accepts_common_labels(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Lock screen.", "Padlock", (120, 160, 110, 32)),
            ("Click the padlock.", "Lock", (120, 160, 80, 32)),
            ("Unlock account.", "Lock", (120, 160, 80, 32)),
            ("Open security.", "Shield", (120, 160, 90, 32)),
            ("Click shield.", "Security", (120, 160, 110, 32)),
            ("Lock screen.", "\U0001f512", (120, 160, 32, 32)),
            ("Open security.", "\U0001f6e1", (120, 160, 32, 32)),
        )
        for instruction, label, rect in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [ControlCandidate("c001", label, "button", rect)],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, rect)

    def test_lock_alias_text_match_overrides_security_settings_geometry(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Lock screen.",
                    "target": {"x": 300, "y": 160, "width": 160, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Padlock", "button", (120, 160, 110, 32)),
                ControlCandidate("c002", "Security settings", "button", (300, 160, 160, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (120, 160, 110, 32))

    def test_security_settings_instruction_targets_settings_not_shield_button(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open security settings.",
                    "target_id": "c001",
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Security settings", "button", (300, 160, 160, 32)),
                ControlCandidate("c002", "Shield", "button", (120, 160, 90, 32)),
            ],
        )

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (300, 160, 160, 32))

    def test_site_information_target_id_accepts_lock_icon_wording(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Click the lock icon.", "View site information", (120, 160, 140, 32)),
            ("Click the padlock icon.", "View site information", (120, 160, 140, 32)),
            ("Click the lock icon.", "\U0001f512", (120, 160, 32, 32)),
        )
        for instruction, label, rect in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            label,
                            "button",
                            rect,
                            window_title="GitHub Dashboard - Google Chrome",
                        )
                    ],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, rect)

    def test_site_information_does_not_snap_generic_view(self) -> None:
        from rect_snap import snap_to_control

        site_info = _make_button(
            "View site information",
            100,
            20,
            160,
            32,
            automation_id="view_1011",
        )
        window = _make_window("GitHub - Google Chrome", 0, 0, 800, 600, [site_info])
        desktop = _FakeDesktop([window])
        model_rect = (100, 20, 160, 32)

        result = snap_to_control(
            model_rect,
            "Open view.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, model_rect)
        self.assertEqual(result.rejected_reason, "candidate semantic mismatch")

    def test_site_information_aliases_do_not_cross_lock_security_or_settings(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Lock screen.", "View site information"),
            ("Unlock account.", "View site information"),
            ("Open security.", "View site information"),
            ("Open site settings.", "View site information"),
            ("Open site information.", "Security settings"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            label,
                            "button",
                            (120, 160, 160, 32),
                            window_title="GitHub Dashboard - Google Chrome",
                        )
                    ],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, "target_id semantic mismatch")

    def test_cart_action_alias_target_id_accepts_common_labels(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Open cart.", "Basket", (120, 160, 100, 32)),
            ("Open basket.", "Cart", (120, 160, 100, 32)),
            ("Open cart.", "Shopping bag", (120, 160, 130, 32)),
            ("Open cart.", "\U0001f6d2", (120, 160, 32, 32)),
        )
        for instruction, label, rect in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [ControlCandidate("c001", label, "button", rect)],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, rect)

    def test_cart_action_alias_text_match_overrides_shopping_options_geometry(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open cart.",
                    "target": {"x": 300, "y": 160, "width": 160, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Basket", "button", (120, 160, 100, 32)),
                ControlCandidate("c002", "Shopping options", "button", (300, 160, 160, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (120, 160, 100, 32))

    def test_cart_alias_rejects_ambiguous_cart_and_basket_actions(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open cart.",
                    "target_id": "c001",
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Basket", "button", (120, 160, 100, 32)),
                ControlCandidate("c002", "Cart", "button", (280, 160, 100, 32)),
            ],
        )

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.target_id, "c001")
        self.assertEqual(target.rejected_reason, "target_id ambiguous")

    def test_password_visibility_target_id_accepts_eye_button(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Show password.", "Eye", (360, 160, 32, 32)),
            ("Hide password.", "Visibility", (360, 160, 80, 32)),
            ("Reveal passcode.", "Eye", (360, 160, 32, 32)),
            ("Show password.", "Show password", (360, 160, 120, 32)),
            ("Hide password.", "Hide password", (360, 160, 120, 32)),
            ("Show password.", "\U0001f441", (360, 160, 32, 32)),
        )
        for instruction, label, rect in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate("c001", label, "button", rect),
                        ControlCandidate("c002", "Password", "edit", (120, 160, 220, 32)),
                    ],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, rect)

    def test_password_visibility_wording_rejects_opposite_state_button(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target
        from help_session import resolve_help_target

        cases = (
            ("Hide password.", "Show password"),
            ("Show password.", "Hide password"),
            ("Conceal password.", "Reveal password"),
            ("Reveal password.", "Conceal password"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                candidates = [
                    ControlCandidate("c001", label, "button", (360, 160, 120, 32)),
                    ControlCandidate("c002", "Password", "edit", (120, 160, 220, 32)),
                ]
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    candidates,
                )
                text_target = resolve_candidate_target(
                    target_id="",
                    instruction=instruction,
                    candidates=[candidates[0]],
                )
                snap_target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target": {"x": 360, "y": 160, "width": 120, "height": 32},
                        }
                    ),
                    self._capture(),
                    candidates,
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, "target_id semantic mismatch")
                self.assertIsNone(text_target)
                self.assertEqual(snap_target.source, "candidate_snap")
                self.assertEqual(snap_target.target_id, "c001")
                self.assertEqual(snap_target.rejected_reason, "candidate semantic mismatch")

    def test_password_visibility_text_match_overrides_password_field_geometry(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Show password.",
                    "target": {"x": 120, "y": 160, "width": 220, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Eye", "button", (360, 160, 32, 32)),
                ControlCandidate("c002", "Password", "edit", (120, 160, 220, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (360, 160, 32, 32))

    def test_show_sidebar_does_not_match_eye_button(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Show sidebar.",
                    "target": {"x": 120, "y": 160, "width": 150, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Eye", "button", (360, 160, 32, 32)),
                ControlCandidate("c002", "Show sidebar", "button", (120, 160, 150, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c002")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (120, 160, 150, 32))

    def test_navigation_and_time_alias_target_id_accepts_common_labels(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Open calendar.", "Date", (120, 160, 100, 32)),
            ("Open date picker.", "Calendar", (120, 160, 120, 32)),
            ("Open clock.", "Time", (120, 160, 100, 32)),
            ("Open time picker.", "Clock", (120, 160, 100, 32)),
            ("Go home.", "House", (120, 160, 100, 32)),
            ("Click the house.", "Home", (120, 160, 100, 32)),
            ("Open calendar.", "\U0001f4c5", (120, 160, 32, 32)),
            ("Go home.", "\U0001f3e0", (120, 160, 32, 32)),
        )
        for instruction, label, rect in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [ControlCandidate("c001", label, "button", rect)],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, rect)

    def test_calendar_alias_text_match_overrides_wrong_geometry(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open calendar.",
                    "target": {"x": 300, "y": 160, "width": 140, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Date", "button", (120, 160, 100, 32)),
                ControlCandidate("c002", "Cancel", "button", (300, 160, 140, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (120, 160, 100, 32))

    def test_home_alias_rejects_ambiguous_home_and_house_actions(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Go home.",
                    "target_id": "c001",
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "House", "button", (120, 160, 100, 32)),
                ControlCandidate("c002", "Home", "button", (280, 160, 100, 32)),
            ],
        )

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.target_id, "c001")
        self.assertEqual(target.rejected_reason, "target_id ambiguous")

    def test_print_action_alias_target_id_accepts_printer_button(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Print document.", "Printer", (120, 160, 100, 32)),
            ("Open printer.", "Print", (120, 160, 100, 32)),
            ("Print document.", "\U0001f5a8", (120, 160, 32, 32)),
        )
        for instruction, label, rect in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [ControlCandidate("c001", label, "button", rect)],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, rect)

    def test_print_action_alias_text_match_overrides_wrong_geometry(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Print document.",
                    "target": {"x": 300, "y": 160, "width": 140, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Printer", "button", (120, 160, 100, 32)),
                ControlCandidate("c002", "Cancel", "button", (300, 160, 140, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (120, 160, 100, 32))

    def test_print_alias_rejects_ambiguous_print_and_printer_actions(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Print document.",
                    "target_id": "c001",
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Printer", "button", (120, 160, 100, 32)),
                ControlCandidate("c002", "Print", "button", (280, 160, 100, 32)),
            ],
        )

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.target_id, "c001")
        self.assertEqual(target.rejected_reason, "target_id ambiguous")

    def test_folder_action_alias_target_id_accepts_directory_button(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Open folder.", "Directory", (120, 160, 120, 32)),
            ("Open directory.", "Folder", (120, 160, 100, 32)),
            ("Open folder.", "\U0001f4c1", (120, 160, 32, 32)),
            ("Open directory.", "\U0001f4c2", (120, 160, 32, 32)),
        )
        for instruction, label, rect in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [ControlCandidate("c001", label, "button", rect)],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, rect)

    def test_folder_action_alias_text_match_overrides_wrong_geometry(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open folder.",
                    "target": {"x": 300, "y": 160, "width": 140, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Directory", "button", (120, 160, 120, 32)),
                ControlCandidate("c002", "Cancel", "button", (300, 160, 140, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (120, 160, 120, 32))

    def test_folder_alias_rejects_ambiguous_folder_and_directory_actions(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open folder.",
                    "target_id": "c001",
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Directory", "button", (120, 160, 120, 32)),
                ControlCandidate("c002", "Folder", "button", (280, 160, 100, 32)),
            ],
        )

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.target_id, "c001")
        self.assertEqual(target.rejected_reason, "target_id ambiguous")

    def test_favorite_action_alias_target_id_accepts_star_button(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Favorite this item.", "Star", (120, 160, 100, 32)),
            ("Star this item.", "Favorite", (120, 160, 120, 32)),
            ("Bookmark this item.", "Star", (120, 160, 100, 32)),
            ("Favorite this item.", "\u2606", (120, 160, 32, 32)),
        )
        for instruction, label, rect in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [ControlCandidate("c001", label, "button", rect)],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, rect)

    def test_favorite_action_alias_text_match_overrides_wrong_geometry(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Favorite this item.",
                    "target": {"x": 300, "y": 160, "width": 100, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Star", "button", (120, 160, 100, 32)),
                ControlCandidate("c002", "Cancel", "button", (300, 160, 100, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (120, 160, 100, 32))

    def test_favorite_alias_rejects_ambiguous_favorite_and_star_actions(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Favorite this item.",
                    "target_id": "c001",
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Star", "button", (120, 160, 100, 32)),
                ControlCandidate("c002", "Favorite", "button", (280, 160, 140, 32)),
            ],
        )

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.target_id, "c001")
        self.assertEqual(target.rejected_reason, "target_id ambiguous")

    def test_favorite_alias_with_exact_neighbor_stays_ambiguous_despite_geometry(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate("star", "Star", "button", (120, 160, 100, 32)),
            ControlCandidate("favorite", "Favorite", "button", (280, 160, 140, 32)),
        ]
        instruction = "Favorite this item."

        wrong_target = resolve_candidate_target(
            target_id="star",
            instruction=instruction,
            candidates=candidates,
            model_rect=(120, 160, 100, 32),
        )
        text_target = resolve_candidate_target(
            target_id="",
            instruction=instruction,
            candidates=candidates,
            model_rect=(120, 160, 100, 32),
        )
        snap_target = snap_candidate_target(
            instruction=instruction,
            candidates=candidates,
            model_rect=(120, 160, 100, 32),
        )
        help_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": instruction,
                    "target_id": "star",
                    "target": {"x": 120, "y": 160, "width": 100, "height": 32},
                }
            ),
            self._capture(),
            candidates,
        )

        self.assertEqual(wrong_target.target_id, "star")
        self.assertEqual(wrong_target.rejected_reason, "target_id ambiguous")
        self.assertEqual(text_target.target_id, "star")
        self.assertEqual(text_target.rejected_reason, "ambiguous text match")
        self.assertIsNone(snap_target)
        self.assertEqual(help_target.target_id, "star")
        self.assertEqual(help_target.rejected_reason, "target_id ambiguous")

    def test_exact_action_neighbor_keeps_alias_geometry_from_clean_highlight(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from help_session import resolve_help_target

        cases = (
            ("Create item.", "add", "Add", "create", "Create"),
            ("Add item.", "create", "Create", "add", "Add"),
            ("New item.", "add", "Add", "new", "New"),
            ("Cancel dialog.", "close", "Close", "cancel", "Cancel"),
            ("Sign out.", "logout_all", "Logout all sessions", "signout", "Sign out"),
            ("Lock account.", "unlock", "Unlock", "lock", "Lock"),
            ("Open cart.", "basket", "Basket", "cart", "Cart"),
            ("Download report.", "export", "Export", "download", "Download"),
            ("Refresh page.", "reload", "Reload", "refresh", "Refresh"),
            ("Search users.", "find", "Find users", "search", "Search users"),
            ("Edit profile.", "pencil", "Pencil", "edit", "Edit"),
            ("Submit form.", "send", "Send", "submit", "Submit"),
            ("Click Done.", "finish", "Finish", "done", "Done"),
        )
        for instruction, wrong_id, wrong_label, right_id, right_label in cases:
            with self.subTest(instruction=instruction):
                candidates = [
                    ControlCandidate(wrong_id, wrong_label, "button", (120, 160, 120, 32)),
                    ControlCandidate(right_id, right_label, "button", (300, 160, 120, 32)),
                ]

                wrong_target = resolve_candidate_target(
                    target_id=wrong_id,
                    instruction=instruction,
                    candidates=candidates,
                    model_rect=(120, 160, 120, 32),
                )
                snap_target = snap_candidate_target(
                    instruction=instruction,
                    candidates=candidates,
                    model_rect=(120, 160, 120, 32),
                )
                help_target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": wrong_id,
                            "target": {"x": 120, "y": 160, "width": 120, "height": 32},
                        }
                    ),
                    self._capture(),
                    candidates,
                )

                self.assertEqual(wrong_target.target_id, wrong_id)
                self.assertEqual(wrong_target.rejected_reason, "target_id ambiguous")
                if snap_target is not None:
                    self.assertIn(snap_target.target_id, {wrong_id, right_id})
                    if snap_target.target_id == wrong_id:
                        self.assertTrue(snap_target.rejected_reason)
                    else:
                        self.assertFalse(snap_target.rejected_reason)
                self.assertIn(help_target.target_id, {wrong_id, right_id})
                if help_target.target_id == wrong_id:
                    self.assertTrue(help_target.rejected_reason)
                else:
                    self.assertFalse(help_target.rejected_reason)

    def test_cardinal_direction_text_match_overrides_opposite_geometry(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from help_session import resolve_help_target

        cases = (
            ("Move down.", "up", "Up", "down", "Down"),
            ("Move up.", "down", "Down", "up", "Up"),
            ("Move left.", "right", "Right", "left", "Left"),
            ("Move right.", "left", "Left", "right", "Right"),
        )
        for instruction, wrong_id, wrong_label, right_id, right_label in cases:
            with self.subTest(instruction=instruction):
                candidates = [
                    ControlCandidate(wrong_id, wrong_label, "button", (120, 160, 100, 32)),
                    ControlCandidate(right_id, right_label, "button", (300, 160, 100, 32)),
                ]

                wrong_target = resolve_candidate_target(
                    target_id=wrong_id,
                    instruction=instruction,
                    candidates=candidates,
                    model_rect=(120, 160, 100, 32),
                )
                text_target = resolve_candidate_target(
                    target_id="",
                    instruction=instruction,
                    candidates=candidates,
                    model_rect=(120, 160, 100, 32),
                )
                snap_target = snap_candidate_target(
                    instruction=instruction,
                    candidates=candidates,
                    model_rect=(120, 160, 100, 32),
                )
                help_target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": wrong_id,
                            "target": {"x": 120, "y": 160, "width": 100, "height": 32},
                        }
                    ),
                    self._capture(),
                    candidates,
                )

                self.assertEqual(wrong_target.target_id, wrong_id)
                self.assertEqual(wrong_target.rejected_reason, "target_id semantic mismatch")
                self.assertEqual(text_target.target_id, right_id)
                self.assertFalse(text_target.rejected_reason)
                if snap_target is not None:
                    self.assertEqual(snap_target.target_id, wrong_id)
                    self.assertEqual(snap_target.rejected_reason, "candidate semantic mismatch")
                self.assertEqual(help_target.target_id, right_id)
                self.assertFalse(help_target.rejected_reason)

    def test_notification_action_alias_target_id_accepts_bell_button(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Click the bell.", "Notifications", (120, 160, 140, 32)),
            ("Open notifications.", "Bell", (120, 160, 100, 32)),
            ("Open alerts.", "Bell", (120, 160, 100, 32)),
            ("Open notifications.", "\U0001f514", (120, 160, 32, 32)),
        )
        for instruction, label, rect in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [ControlCandidate("c001", label, "button", rect)],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, rect)

    def test_notification_action_alias_text_match_overrides_wrong_geometry(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click the bell.",
                    "target": {"x": 300, "y": 160, "width": 100, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Notifications", "button", (120, 160, 140, 32)),
                ControlCandidate("c002", "Cancel", "button", (300, 160, 100, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (120, 160, 140, 32))

    def test_notification_settings_recovers_from_taskbar_notification_status(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate(
                "notif",
                "Notifications",
                "button",
                (900, 940, 120, 32),
                automation_id="SystemTrayIcon",
                window_title="Taskbar",
            ),
            ControlCandidate(
                "manage",
                "Manage notifications",
                "button",
                (420, 180, 180, 32),
                window_title="Notifications",
            ),
        ]
        instruction = "Open notification settings."

        wrong_target = resolve_candidate_target(
            target_id="notif",
            instruction=instruction,
            candidates=candidates,
            model_rect=(900, 940, 120, 32),
        )
        text_target = resolve_candidate_target(
            target_id="",
            instruction=instruction,
            candidates=candidates,
            model_rect=(900, 940, 120, 32),
        )
        snap_target = snap_candidate_target(
            instruction=instruction,
            candidates=candidates,
            model_rect=(900, 940, 120, 32),
        )
        help_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": instruction,
                    "target_id": "notif",
                    "target": {"x": 900, "y": 940, "width": 120, "height": 32},
                }
            ),
            self._capture(),
            candidates,
        )

        self.assertEqual(wrong_target.target_id, "notif")
        self.assertEqual(wrong_target.rejected_reason, "target_id semantic mismatch")
        self.assertEqual(text_target.target_id, "manage")
        self.assertFalse(text_target.rejected_reason)
        self.assertIsNone(snap_target)
        self.assertEqual(help_target.target_id, "manage")
        self.assertFalse(help_target.rejected_reason)

    def test_notification_alias_rejects_ambiguous_bell_and_notifications_actions(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open notifications.",
                    "target_id": "c001",
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Bell", "button", (120, 160, 100, 32)),
                ControlCandidate("c002", "Notifications", "button", (280, 160, 140, 32)),
            ],
        )

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.target_id, "c001")
        self.assertEqual(target.rejected_reason, "target_id ambiguous")

    def test_system_tray_target_id_accepts_show_hidden_icons(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            "Open system tray.",
            "Open notification area.",
            "Show hidden icons.",
            "Open hidden icons.",
        )
        for instruction in cases:
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            "Show Hidden Icons",
                            "button",
                            (120, 160, 32, 32),
                            window_title="Taskbar",
                        )
                    ],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, (120, 160, 32, 32))

    def test_show_hidden_files_accepts_exact_visibility_object_label(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate(
                "c001",
                "Show hidden files",
                "checkbox",
                (120, 160, 180, 32),
                window_title="File Explorer Options",
            )
        ]
        instruction = "Show hidden files."

        target_id = resolve_candidate_target(
            target_id="c001",
            instruction=instruction,
            candidates=candidates,
            model_rect=(120, 160, 180, 32),
        )
        text_target = resolve_candidate_target(
            target_id="",
            instruction=instruction,
            candidates=candidates,
            model_rect=(120, 160, 180, 32),
        )
        snap_target = snap_candidate_target(
            instruction=instruction,
            candidates=candidates,
            model_rect=(120, 160, 180, 32),
        )
        help_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": instruction,
                    "target_id": "c001",
                    "target": {"x": 120, "y": 160, "width": 180, "height": 32},
                }
            ),
            self._capture(),
            candidates,
        )

        self.assertEqual(target_id.source, "target_id")
        self.assertEqual(target_id.target_id, "c001")
        self.assertFalse(target_id.rejected_reason)
        self.assertEqual(text_target.target_id, "c001")
        self.assertFalse(text_target.rejected_reason)
        self.assertEqual(snap_target.target_id, "c001")
        self.assertFalse(snap_target.rejected_reason)
        self.assertEqual(help_target.source, "target_id")
        self.assertEqual(help_target.target_id, "c001")
        self.assertFalse(help_target.rejected_reason)
        self.assertEqual(help_target.rect, (120, 160, 180, 32))

    def test_system_tray_aliases_do_not_cross_notifications_or_generic_show(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Open notifications.", "Show Hidden Icons"),
            ("Open notification area.", "Bell"),
            ("Open system tray.", "System Settings"),
            ("Open hidden.", "Show Hidden Icons"),
            ("Click hidden.", "Show Hidden Icons"),
            ("Open icons.", "Show Hidden Icons"),
            ("Click icons.", "Show Hidden Icons"),
            ("Show history.", "Show Hidden Icons"),
            ("Show password.", "Show Hidden Icons"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            label,
                            "button",
                            (120, 160, 140, 32),
                            window_title="Taskbar",
                        )
                    ],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, "target_id semantic mismatch")

    def test_bare_hidden_text_match_ignores_show_hidden_icons(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        for instruction in ("Open hidden.", "Open icons."):
            with self.subTest(instruction=instruction):
                result = resolve_candidate_target(
                    target_id="",
                    instruction=instruction,
                    candidates=[
                        ControlCandidate(
                            "c001",
                            "Show Hidden Icons",
                            "button",
                            (120, 160, 32, 32),
                            window_title="Taskbar",
                        )
                    ],
                )

                self.assertIsNone(result)

    def test_bare_hidden_model_rect_rejects_show_hidden_icons_snap(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        for instruction in ("Open hidden.", "Open icons."):
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target": {"x": 120, "y": 160, "width": 32, "height": 32},
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            "Show Hidden Icons",
                            "button",
                            (120, 160, 32, 32),
                            window_title="Taskbar",
                        )
                    ],
                )

                self.assertEqual(target.source, "candidate_snap")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, "candidate semantic mismatch")

    def test_network_target_id_accepts_wifi_language(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            "Open Wi-Fi.",
            "Open wifi.",
            "Open wireless.",
            "Open StarLink.",
        )
        for instruction in cases:
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            "Network StarLink\nInternet access",
                            "button",
                            (120, 160, 140, 32),
                            window_title="Taskbar",
                        )
                    ],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, (120, 160, 140, 32))

    def test_network_starlink_does_not_cross_bookmark_or_favorite_actions(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            "Favorite this item.",
            "Bookmark this.",
            "Open Wi-Fi.",
        )
        labels = {
            "Favorite this item.": "Network StarLink\nInternet access",
            "Bookmark this.": "Network StarLink\nInternet access",
            "Open Wi-Fi.": "Airplane mode",
        }
        for instruction in cases:
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            labels[instruction],
                            "button",
                            (120, 160, 140, 32),
                            window_title="Taskbar",
                        )
                    ],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, "target_id semantic mismatch")

    def test_disclosure_broad_row_highlights_single_button(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Click the chevron.", "Expand", "c002"),
            ("Expand Advanced settings.", "Expand", "c002"),
            ("Collapse Advanced settings.", "Collapse", "c003"),
        )
        for instruction, label, candidate_id in cases:
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target": {"x": 120, "y": 160, "width": 500, "height": 80},
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate("c001", "Advanced settings", "listitem", (120, 160, 500, 80)),
                        ControlCandidate(candidate_id, label, "button", (578, 186, 28, 28)),
                    ],
                )

                self.assertEqual(target.source, "text_match")
                self.assertEqual(target.target_id, candidate_id)
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, (578, 186, 28, 28))

    def test_row_scoped_action_model_rect_promotes_contained_button(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            (
                "Click Pay for Beta invoice row.",
                [
                    ControlCandidate("r1", "INV-001 Acme Pending", "listitem", (10, 10, 800, 40)),
                    ControlCandidate("pay1", "Pay", "button", (720, 14, 60, 30)),
                    ControlCandidate("r2", "INV-002 Beta Pending", "listitem", (10, 60, 800, 40)),
                    ControlCandidate("pay2", "Pay", "button", (720, 64, 60, 30)),
                ],
                "pay2",
                (720, 64, 60, 30),
                "text_match",
            ),
            (
                "Click More in Bob row.",
                [
                    ControlCandidate("r1", "Alice", "listitem", (10, 10, 800, 40)),
                    ControlCandidate("more1", "More", "button", (720, 14, 60, 30)),
                    ControlCandidate("r2", "Bob", "listitem", (10, 60, 800, 40)),
                    ControlCandidate("more2", "More", "button", (720, 64, 60, 30)),
                ],
                "more2",
                (720, 64, 60, 30),
                "text_match",
            ),
        )
        for instruction, candidates, candidate_id, rect, source in cases:
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target": {"x": 10, "y": 60, "width": 800, "height": 40},
                        }
                    ),
                    self._capture(),
                    candidates,
                )

                self.assertEqual(target.source, source)
                self.assertEqual(target.target_id, candidate_id)
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, rect)

    def test_row_scoped_action_matches_adjacent_action_column(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate("r1", "Alpha", "listitem", (10, 10, 600, 40)),
            ControlCandidate("pay1", "Pay", "button", (620, 14, 60, 30)),
            ControlCandidate("r2", "Beta", "listitem", (10, 60, 600, 40)),
            ControlCandidate("pay2", "Pay", "button", (620, 64, 60, 30)),
        ]
        instruction = "Click Pay for Beta row."
        target_id = resolve_candidate_target(
            target_id="pay2",
            instruction=instruction,
            candidates=candidates,
            model_rect=(620, 64, 60, 30),
        )
        text_target = resolve_candidate_target(
            target_id="",
            instruction=instruction,
            candidates=candidates,
            model_rect=(10, 60, 600, 40),
        )
        help_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": instruction,
                    "target": {"x": 10, "y": 60, "width": 600, "height": 40},
                }
            ),
            self._capture(),
            candidates,
        )

        self.assertEqual(target_id.source, "target_id")
        self.assertEqual(target_id.target_id, "pay2")
        self.assertFalse(target_id.rejected_reason)
        for resolved in (text_target, help_target):
            self.assertEqual(resolved.source, "text_match")
            self.assertEqual(resolved.target_id, "pay2")
            self.assertFalse(resolved.rejected_reason)
            self.assertEqual(resolved.rect, (620, 64, 60, 30))

    def test_row_scoped_action_matches_automation_only_button(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate("r1", "Alpha", "listitem", (10, 10, 800, 40)),
            ControlCandidate("pay1", "", "button", (720, 14, 32, 30), automation_id="payButton"),
            ControlCandidate("r2", "Beta", "listitem", (10, 60, 800, 40)),
            ControlCandidate("pay2", "", "button", (720, 64, 32, 30), automation_id="payButton"),
        ]
        instruction = "Click Pay for Beta row."
        target_id = resolve_candidate_target(
            target_id="pay2",
            instruction=instruction,
            candidates=candidates,
            model_rect=(720, 64, 32, 30),
        )
        help_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": instruction,
                    "target": {"x": 10, "y": 60, "width": 800, "height": 40},
                }
            ),
            self._capture(),
            candidates,
        )

        self.assertEqual(target_id.source, "target_id")
        self.assertEqual(target_id.target_id, "pay2")
        self.assertFalse(target_id.rejected_reason)
        self.assertEqual(help_target.source, "text_match")
        self.assertEqual(help_target.target_id, "pay2")
        self.assertFalse(help_target.rejected_reason)
        self.assertEqual(help_target.rect, (720, 64, 32, 30))

    def test_row_scoped_action_matches_singular_item_context(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target
        from help_session import resolve_help_target

        for noun in ("item", "record", "entry", "result"):
            with self.subTest(noun=noun):
                candidates = [
                    ControlCandidate("row_acme", f"Acme {noun}", "listitem", (20, 90, 560, 48)),
                    ControlCandidate("refund_acme", "Refund", "button", (610, 99, 80, 30)),
                    ControlCandidate("row_globex", f"Globex {noun}", "listitem", (20, 150, 560, 48)),
                    ControlCandidate("refund_globex", "Refund", "button", (610, 159, 80, 30)),
                ]
                instruction = f"Click Refund for Globex {noun}."
                wrong_target = resolve_candidate_target(
                    target_id="refund_acme",
                    instruction=instruction,
                    candidates=candidates,
                    model_rect=(610, 99, 80, 30),
                )
                text_target = resolve_candidate_target(
                    target_id="",
                    instruction=instruction,
                    candidates=candidates,
                    model_rect=(610, 99, 80, 30),
                )
                help_target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "refund_acme",
                            "target": {"x": 610, "y": 99, "width": 80, "height": 30},
                        }
                    ),
                    self._capture(),
                    candidates,
                )

                self.assertEqual(wrong_target.target_id, "refund_acme")
                self.assertEqual(wrong_target.rejected_reason, "target_id semantic mismatch")
                for resolved in (text_target, help_target):
                    self.assertEqual(resolved.target_id, "refund_globex")
                    self.assertFalse(resolved.rejected_reason)
                    self.assertEqual(resolved.rect, (610, 159, 80, 30))

    def test_row_scoped_action_without_row_evidence_rejects_duplicate_action(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate("email_save", "Save", "button", (220, 80, 70, 30)),
            ControlCandidate("phone_save", "Save", "button", (220, 130, 70, 30)),
        ]
        instruction = "Click Save for Phone record."

        wrong_target = resolve_candidate_target(
            target_id="email_save",
            instruction=instruction,
            candidates=candidates,
            model_rect=(220, 80, 70, 30),
        )
        text_target = resolve_candidate_target(
            target_id="",
            instruction=instruction,
            candidates=candidates,
            model_rect=(220, 80, 70, 30),
        )
        snap_target = snap_candidate_target(
            instruction=instruction,
            candidates=candidates,
            model_rect=(220, 80, 70, 30),
        )
        help_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": instruction,
                    "target_id": "email_save",
                    "target": {"x": 220, "y": 80, "width": 70, "height": 30},
                }
            ),
            self._capture(),
            candidates,
        )

        self.assertEqual(wrong_target.target_id, "email_save")
        self.assertEqual(wrong_target.rejected_reason, "target_id semantic mismatch")
        self.assertIsNone(text_target)
        self.assertEqual(snap_target.target_id, "email_save")
        self.assertEqual(snap_target.rejected_reason, "candidate semantic mismatch")
        self.assertEqual(help_target.target_id, "email_save")
        self.assertEqual(help_target.rejected_reason, "target_id semantic mismatch")

    def test_row_scoped_action_uses_named_row_label_without_row_noun(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate("r1", "Acme", "listitem", (20, 90, 560, 48)),
            ControlCandidate("refund1", "Refund", "button", (610, 99, 80, 30)),
            ControlCandidate("r2", "Globex", "listitem", (20, 150, 560, 48)),
            ControlCandidate("refund2", "Refund", "button", (610, 159, 80, 30)),
        ]
        instruction = "Click Refund for Globex."

        wrong_target = resolve_candidate_target(
            target_id="refund1",
            instruction=instruction,
            candidates=candidates,
            model_rect=(610, 99, 80, 30),
        )
        text_target = resolve_candidate_target(
            target_id="",
            instruction=instruction,
            candidates=candidates,
            model_rect=(610, 99, 80, 30),
        )
        snap_target = snap_candidate_target(
            instruction=instruction,
            candidates=candidates,
            model_rect=(610, 99, 80, 30),
        )
        help_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": instruction,
                    "target_id": "refund1",
                    "target": {"x": 610, "y": 99, "width": 80, "height": 30},
                }
            ),
            self._capture(),
            candidates,
        )

        self.assertEqual(wrong_target.target_id, "refund1")
        self.assertEqual(wrong_target.rejected_reason, "target_id semantic mismatch")
        for resolved in (text_target, snap_target, help_target):
            self.assertEqual(resolved.target_id, "refund2")
            self.assertFalse(resolved.rejected_reason)
            self.assertEqual(resolved.rect, (610, 159, 80, 30))

    def test_row_scoped_action_wrong_target_id_recovers_to_filtered_action_word(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate("r1", "Alice", "listitem", (10, 10, 600, 80)),
            ControlCandidate("a1", "Open", "button", (520, 34, 80, 30)),
            ControlCandidate("r2", "Bob", "listitem", (10, 100, 600, 80)),
            ControlCandidate("a2", "Open", "button", (520, 124, 80, 30)),
        ]
        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Open in Bob row.",
                    "target_id": "a1",
                    "target": {"x": 520, "y": 34, "width": 80, "height": 30},
                }
            ),
            self._capture(),
            candidates,
        )
        text_target = resolve_candidate_target(
            target_id="",
            instruction="Click Open in Bob row.",
            candidates=candidates,
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "a2")
        self.assertEqual(target.rect, (520, 124, 80, 30))
        self.assertFalse(target.rejected_reason)
        self.assertIsNotNone(text_target)
        assert text_target is not None
        self.assertEqual(text_target.target_id, "a2")

    def test_row_scoped_action_target_id_uses_context_over_wrong_model_rect(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            (
                "Click Archive in Order 2 row.",
                {
                    "x": 10,
                    "y": 10,
                    "width": 800,
                    "height": 40,
                },
                [
                    ControlCandidate("r1", "Order 1", "listitem", (10, 10, 800, 40)),
                    ControlCandidate("a1", "Archive", "button", (720, 14, 80, 30)),
                    ControlCandidate("r2", "Order 2", "listitem", (10, 60, 800, 40)),
                    ControlCandidate("a2", "Archive", "button", (720, 64, 80, 30)),
                ],
                "a2",
                (720, 64, 80, 30),
            ),
            (
                "Click Archive on Project B list item.",
                {
                    "x": 10,
                    "y": 10,
                    "width": 300,
                    "height": 120,
                },
                [
                    ControlCandidate("r1", "Project A", "listitem", (10, 10, 300, 120)),
                    ControlCandidate("a1", "Archive", "button", (220, 90, 80, 30)),
                    ControlCandidate("r2", "Project B", "listitem", (350, 10, 300, 120)),
                    ControlCandidate("a2", "Archive", "button", (560, 90, 80, 30)),
                ],
                "a2",
                (560, 90, 80, 30),
            ),
        )
        for instruction, target_rect, candidates, expected_id, expected_rect in cases:
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": expected_id,
                            "target": target_rect,
                        }
                    ),
                    self._capture(),
                    candidates,
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, expected_id)
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, expected_rect)

    def test_contextual_duplicate_scopes_require_requested_position_or_surface(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target

        cases = (
            (
                "Click Save in the second Billing card.",
                "save1",
                "save2",
                [
                    ControlCandidate("card1", "Billing", "listitem", (10, 10, 300, 100)),
                    ControlCandidate("save1", "Save", "button", (230, 70, 60, 30)),
                    ControlCandidate("card2", "Billing", "listitem", (10, 130, 300, 100)),
                    ControlCandidate("save2", "Save", "button", (230, 190, 60, 30)),
                ],
            ),
            (
                "Click Delete in the right Details section.",
                "del1",
                "del2",
                [
                    ControlCandidate("leftsec", "Details", "listitem", (10, 10, 300, 100)),
                    ControlCandidate("del1", "Delete", "button", (230, 70, 60, 30)),
                    ControlCandidate("rightsec", "Details", "listitem", (350, 10, 300, 100)),
                    ControlCandidate("del2", "Delete", "button", (570, 70, 60, 30)),
                ],
            ),
            (
                "Click Sort in the Name column.",
                "sort_age",
                "sort_name",
                [
                    ControlCandidate("name_col", "Name", "headeritem", (10, 10, 200, 40)),
                    ControlCandidate("sort_name", "Sort", "button", (170, 16, 32, 28)),
                    ControlCandidate("age_col", "Age", "headeritem", (220, 10, 200, 40)),
                    ControlCandidate("sort_age", "Sort", "button", (380, 16, 32, 28)),
                ],
            ),
            (
                "Click Approve for Globex in the Production column.",
                "globex_stage",
                "globex_prod",
                [
                    ControlCandidate("stage_col", "Staging", "headeritem", (220, 40, 160, 36)),
                    ControlCandidate("prod_col", "Production", "headeritem", (400, 40, 160, 36)),
                    ControlCandidate("acme_row", "Acme Corp", "listitem", (20, 90, 560, 48)),
                    ControlCandidate("acme_stage", "Approve", "button", (270, 100, 80, 28)),
                    ControlCandidate("acme_prod", "Approve", "button", (450, 100, 80, 28)),
                    ControlCandidate("globex_row", "Globex Corp", "listitem", (20, 150, 560, 48)),
                    ControlCandidate("globex_stage", "Approve", "button", (270, 160, 80, 28)),
                    ControlCandidate("globex_prod", "Approve", "button", (450, 160, 80, 28)),
                ],
            ),
            (
                "Click Save in the toolbar.",
                "ft_save",
                "tb_save",
                [
                    ControlCandidate("toolbar", "Main toolbar", "toolbar", (0, 0, 800, 48)),
                    ControlCandidate("tb_save", "Save", "button", (20, 8, 60, 30)),
                    ControlCandidate("footer", "Footer", "pane", (0, 500, 800, 80)),
                    ControlCandidate("ft_save", "Save", "button", (20, 520, 60, 30)),
                ],
            ),
            (
                "Click Settings in the account menu.",
                "side_settings",
                "menu_settings",
                [
                    ControlCandidate("sidebar", "Sidebar", "pane", (0, 50, 180, 500)),
                    ControlCandidate("side_settings", "Settings", "button", (20, 80, 100, 30)),
                    ControlCandidate("menu", "Account menu", "menu", (500, 50, 180, 200)),
                    ControlCandidate("menu_settings", "Settings", "button", (520, 80, 100, 30)),
                ],
            ),
            (
                "Click Save in the card.",
                "modal_save",
                "card_save",
                [
                    ControlCandidate("card", "Profile card", "listitem", (20, 80, 300, 100)),
                    ControlCandidate("card_save", "Save", "button", (230, 140, 60, 30)),
                    ControlCandidate("modal", "Save changes modal", "window", (360, 200, 280, 160)),
                    ControlCandidate("modal_save", "Save", "button", (480, 310, 60, 30)),
                ],
            ),
            (
                "Click Archive in the Billing card.",
                "profile_archive",
                "billing_archive",
                [
                    ControlCandidate("profile_card", "Profile card", "listitem", (20, 80, 300, 100)),
                    ControlCandidate("profile_archive", "Archive", "button", (240, 140, 70, 30)),
                    ControlCandidate("billing_card", "Billing card", "listitem", (360, 80, 300, 100)),
                    ControlCandidate("billing_archive", "Archive", "button", (580, 140, 70, 30)),
                ],
            ),
            (
                "Click Save in the modal.",
                "page_save",
                "modal_save",
                [
                    ControlCandidate(
                        "page_save",
                        "Save",
                        "button",
                        (100, 100, 70, 30),
                        window_title="Main page",
                        window_rank=0,
                    ),
                    ControlCandidate(
                        "modal_save",
                        "Save",
                        "button",
                        (500, 300, 70, 30),
                        window_title="Save changes",
                        window_rank=1,
                    ),
                ],
            ),
            (
                "Click Save in the modal.",
                "page_save",
                "modal_save",
                [
                    ControlCandidate("modal_window", "Confirm changes modal", "window", (420, 80, 300, 120), window_rank=0),
                    ControlCandidate(
                        "modal_save",
                        "Save",
                        "button",
                        (630, 160, 60, 30),
                        window_title="Confirm changes",
                        window_rank=0,
                    ),
                    ControlCandidate(
                        "page_save",
                        "Save",
                        "button",
                        (230, 160, 60, 30),
                        window_title="Editor",
                        window_rank=1,
                    ),
                ],
            ),
            (
                "Click Save in the panel.",
                "section_save",
                "panel_save",
                [
                    ControlCandidate("panel", "Details panel", "pane", (20, 80, 300, 100)),
                    ControlCandidate("panel_save", "Save", "button", (230, 140, 60, 30)),
                    ControlCandidate("section", "Details section", "pane", (360, 80, 300, 100)),
                    ControlCandidate("section_save", "Save", "button", (570, 140, 60, 30)),
                ],
            ),
            (
                "Click Save in the section.",
                "panel_save",
                "section_save",
                [
                    ControlCandidate("panel", "Details panel", "pane", (20, 80, 300, 100)),
                    ControlCandidate("panel_save", "Save", "button", (230, 140, 60, 30)),
                    ControlCandidate("section", "Details section", "pane", (360, 80, 300, 100)),
                    ControlCandidate("section_save", "Save", "button", (570, 140, 60, 30)),
                ],
            ),
            (
                "Click Save in the sidebar.",
                "main_save",
                "sidebar_save",
                [
                    ControlCandidate("main", "Main content", "pane", (20, 80, 300, 120)),
                    ControlCandidate("main_save", "Save", "button", (230, 160, 60, 30)),
                    ControlCandidate("sidebar", "Settings sidebar", "pane", (420, 80, 300, 120)),
                    ControlCandidate("sidebar_save", "Save", "button", (630, 160, 60, 30)),
                ],
            ),
            (
                "Click Save in the drawer.",
                "main_save",
                "drawer_save",
                [
                    ControlCandidate("main", "Main content", "pane", (20, 80, 300, 120)),
                    ControlCandidate("main_save", "Save", "button", (230, 160, 60, 30)),
                    ControlCandidate("drawer", "Settings drawer", "pane", (420, 80, 300, 120)),
                    ControlCandidate("drawer_save", "Save", "button", (630, 160, 60, 30)),
                ],
            ),
            (
                "Click Save in the drawers.",
                "main_save",
                "drawer_save",
                [
                    ControlCandidate("main", "Main content", "pane", (20, 80, 300, 120)),
                    ControlCandidate("main_save", "Save", "button", (230, 160, 60, 30)),
                    ControlCandidate("drawer", "Settings drawers", "pane", (420, 80, 300, 120)),
                    ControlCandidate("drawer_save", "Save", "button", (630, 160, 60, 30)),
                ],
            ),
            (
                "Click Save in the popover.",
                "main_save",
                "popover_save",
                [
                    ControlCandidate("main", "Main content", "pane", (20, 80, 300, 120)),
                    ControlCandidate("main_save", "Save", "button", (230, 160, 60, 30)),
                    ControlCandidate("popover", "Settings popover", "pane", (420, 80, 300, 120)),
                    ControlCandidate("popover_save", "Save", "button", (630, 160, 60, 30)),
                ],
            ),
            (
                "Click Save in the popup.",
                "main_save",
                "popup_save",
                [
                    ControlCandidate("main", "Main content", "pane", (20, 80, 300, 120)),
                    ControlCandidate("main_save", "Save", "button", (230, 160, 60, 30)),
                    ControlCandidate("popup", "Settings popup", "window", (420, 80, 300, 120)),
                    ControlCandidate("popup_save", "Save", "button", (630, 160, 60, 30)),
                ],
            ),
            (
                "Click Save in the popup.",
                "main_save",
                "popup_save",
                [
                    ControlCandidate("main", "Main content", "pane", (20, 80, 300, 120)),
                    ControlCandidate("main_save", "Save", "button", (230, 160, 60, 30)),
                    ControlCandidate("popup", "Settings popup", "window", (420, 80, 300, 120)),
                    ControlCandidate(
                        "popup_save",
                        "",
                        "button",
                        (630, 160, 32, 32),
                        automation_id="save_button",
                    ),
                ],
            ),
            (
                "Click Save in the toast.",
                "main_save",
                "toast_save",
                [
                    ControlCandidate("main", "Main content", "pane", (20, 80, 300, 120)),
                    ControlCandidate("main_save", "Save", "button", (230, 160, 60, 30)),
                    ControlCandidate("toast", "Settings toast", "pane", (420, 80, 300, 120)),
                    ControlCandidate("toast_save", "Save", "button", (630, 160, 60, 30)),
                ],
            ),
            (
                "Click Save in the banner.",
                "main_save",
                "banner_save",
                [
                    ControlCandidate("main", "Main content", "pane", (20, 80, 300, 120)),
                    ControlCandidate("main_save", "Save", "button", (230, 160, 60, 30)),
                    ControlCandidate("banner", "Settings banner", "pane", (420, 80, 300, 120)),
                    ControlCandidate("banner_save", "Save", "button", (630, 160, 60, 30)),
                ],
            ),
            (
                "Click Save in the notification.",
                "main_save",
                "notification_save",
                [
                    ControlCandidate("main", "Main content", "pane", (20, 80, 300, 120)),
                    ControlCandidate("main_save", "Save", "button", (230, 160, 60, 30)),
                    ControlCandidate("notification", "Settings notification", "pane", (420, 80, 300, 120)),
                    ControlCandidate("notification_save", "Save", "button", (630, 160, 60, 30)),
                ],
            ),
            (
                "Click Save in the notifications.",
                "main_save",
                "notification_save",
                [
                    ControlCandidate("main", "Main content", "pane", (20, 80, 300, 120)),
                    ControlCandidate("main_save", "Save", "button", (230, 160, 60, 30)),
                    ControlCandidate("notification", "Settings notifications", "pane", (420, 80, 300, 120)),
                    ControlCandidate("notification_save", "Save", "button", (630, 160, 60, 30)),
                ],
            ),
        )
        for instruction, wrong_id, correct_id, candidates in cases:
            with self.subTest(instruction=instruction):
                wrong = next(candidate for candidate in candidates if candidate.id == wrong_id)
                correct = next(candidate for candidate in candidates if candidate.id == correct_id)

                wrong_target = resolve_candidate_target(
                    target_id=wrong_id,
                    instruction=instruction,
                    candidates=candidates,
                    model_rect=wrong.rect,
                )
                wrong_snap = snap_candidate_target(
                    instruction=instruction,
                    candidates=candidates,
                    model_rect=wrong.rect,
                )
                correct_target = resolve_candidate_target(
                    target_id=correct_id,
                    instruction=instruction,
                    candidates=candidates,
                    model_rect=wrong.rect,
                )

                self.assertIsNotNone(wrong_target)
                assert wrong_target is not None
                self.assertIn(
                    wrong_target.rejected_reason,
                    {"target_id ambiguous", "target_id semantic mismatch"},
                )
                if wrong_snap is not None:
                    if wrong_snap.target_id == correct_id:
                        self.assertFalse(wrong_snap.rejected_reason)
                    else:
                        self.assertEqual(wrong_snap.rejected_reason, "candidate semantic mismatch")
                self.assertIsNotNone(correct_target)
                assert correct_target is not None
                self.assertEqual(correct_target.source, "target_id")
                self.assertEqual(correct_target.target_id, correct_id)
                self.assertFalse(correct_target.rejected_reason)
                self.assertEqual(correct_target.rect, correct.rect)

    def test_matrix_scoped_duplicate_action_uses_row_and_column_context(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate("stage_col", "Staging", "headeritem", (220, 40, 160, 36)),
            ControlCandidate("prod_col", "Production", "headeritem", (400, 40, 160, 36)),
            ControlCandidate("acme_row", "Acme Corp", "listitem", (20, 90, 560, 48)),
            ControlCandidate("acme_stage", "Approve", "button", (270, 100, 80, 28)),
            ControlCandidate("acme_prod", "Approve", "button", (450, 100, 80, 28)),
            ControlCandidate("globex_row", "Globex Corp", "listitem", (20, 150, 560, 48)),
            ControlCandidate("globex_stage", "Approve", "button", (270, 160, 80, 28)),
            ControlCandidate("globex_prod", "Approve", "button", (450, 160, 80, 28)),
        ]

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Approve for Globex in the Production column.",
                    "target_id": "globex_stage",
                    "target": {"x": 270, "y": 160, "width": 80, "height": 28},
                }
            ),
            self._capture(),
            candidates,
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "globex_prod")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (450, 160, 80, 28))

    def test_shorthand_header_context_recovers_requested_column_action(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate("name_col", "Name", "headeritem", (20, 40, 160, 36)),
            ControlCandidate("status_col", "Status", "headeritem", (200, 40, 160, 36)),
            ControlCandidate("name_filter", "Filter", "button", (140, 86, 70, 28)),
            ControlCandidate("status_filter", "Filter", "button", (320, 86, 70, 28)),
        ]
        instruction = "Click Filter Status."

        wrong_target = resolve_candidate_target(
            target_id="name_filter",
            instruction=instruction,
            candidates=candidates,
            model_rect=(140, 86, 70, 28),
        )
        text_target = resolve_candidate_target(
            target_id="",
            instruction=instruction,
            candidates=candidates,
            model_rect=(140, 86, 70, 28),
        )
        snap_target = snap_candidate_target(
            instruction=instruction,
            candidates=candidates,
            model_rect=(140, 86, 70, 28),
        )
        help_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": instruction,
                    "target_id": "name_filter",
                    "target": {"x": 140, "y": 86, "width": 70, "height": 28},
                }
            ),
            self._capture(),
            candidates,
        )

        self.assertEqual(wrong_target.target_id, "name_filter")
        self.assertEqual(wrong_target.rejected_reason, "target_id ambiguous")
        self.assertEqual(text_target.target_id, "status_filter")
        self.assertFalse(text_target.rejected_reason)
        self.assertIsNone(snap_target)
        self.assertEqual(help_target.source, "text_match")
        self.assertEqual(help_target.target_id, "status_filter")
        self.assertFalse(help_target.rejected_reason)
        self.assertEqual(help_target.rect, (320, 86, 70, 28))

    def test_shorthand_container_context_recovers_requested_duplicate_action(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from help_session import resolve_help_target

        cases = (
            (
                "Review Bob.",
                "review_alice",
                "review_bob",
                [
                    ControlCandidate("row_alice", "Alice", "listitem", (20, 80, 500, 48)),
                    ControlCandidate("review_alice", "Review", "button", (540, 88, 90, 32)),
                    ControlCandidate("row_bob", "Bob", "listitem", (20, 140, 500, 48)),
                    ControlCandidate("review_bob", "Review", "button", (540, 148, 90, 32)),
                ],
            ),
            (
                "Save Billing.",
                "profile_save",
                "billing_save",
                [
                    ControlCandidate("profile_card", "Profile", "pane", (20, 80, 300, 100)),
                    ControlCandidate("profile_save", "Save", "button", (240, 140, 70, 30)),
                    ControlCandidate("billing_card", "Billing", "pane", (360, 80, 300, 100)),
                    ControlCandidate("billing_save", "Save", "button", (580, 140, 70, 30)),
                ],
            ),
        )
        for instruction, wrong_id, expected_id, candidates in cases:
            with self.subTest(instruction=instruction):
                wrong = next(candidate for candidate in candidates if candidate.id == wrong_id)
                expected = next(candidate for candidate in candidates if candidate.id == expected_id)

                wrong_target = resolve_candidate_target(
                    target_id=wrong_id,
                    instruction=instruction,
                    candidates=candidates,
                    model_rect=wrong.rect,
                )
                text_target = resolve_candidate_target(
                    target_id="",
                    instruction=instruction,
                    candidates=candidates,
                    model_rect=wrong.rect,
                )
                snap_target = snap_candidate_target(
                    instruction=instruction,
                    candidates=candidates,
                    model_rect=wrong.rect,
                )
                help_target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": wrong_id,
                            "target": {
                                "x": wrong.rect[0],
                                "y": wrong.rect[1],
                                "width": wrong.rect[2],
                                "height": wrong.rect[3],
                            },
                        }
                    ),
                    self._capture(),
                    candidates,
                )

                self.assertEqual(wrong_target.target_id, wrong_id)
                self.assertEqual(wrong_target.rejected_reason, "target_id semantic mismatch")
                self.assertEqual(text_target.target_id, expected_id)
                self.assertFalse(text_target.rejected_reason)
                if snap_target is not None:
                    self.assertNotEqual(snap_target.target_id, wrong_id)
                self.assertEqual(help_target.source, "text_match")
                self.assertEqual(help_target.target_id, expected_id)
                self.assertFalse(help_target.rejected_reason)
                self.assertEqual(help_target.rect, expected.rect)

    def test_window_context_recovers_requested_duplicate_action(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate("alpha_window", "Alpha window", "window", (20, 80, 300, 100)),
            ControlCandidate("alpha_duplicate", "Duplicate", "button", (240, 140, 80, 32)),
            ControlCandidate("beta_window", "Beta window", "window", (420, 80, 300, 100)),
            ControlCandidate("beta_duplicate", "Duplicate", "button", (640, 140, 80, 32)),
        ]
        instruction = "Use Duplicate on the Beta window."

        wrong_target = resolve_candidate_target(
            target_id="alpha_duplicate",
            instruction=instruction,
            candidates=candidates,
            model_rect=(240, 140, 80, 32),
        )
        text_target = resolve_candidate_target(
            target_id="",
            instruction=instruction,
            candidates=candidates,
            model_rect=(240, 140, 80, 32),
        )
        snap_target = snap_candidate_target(
            instruction=instruction,
            candidates=candidates,
            model_rect=(240, 140, 80, 32),
        )
        help_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": instruction,
                    "target_id": "alpha_duplicate",
                    "target": {"x": 240, "y": 140, "width": 80, "height": 32},
                }
            ),
            self._capture(),
            candidates,
        )

        self.assertEqual(wrong_target.target_id, "alpha_duplicate")
        self.assertEqual(wrong_target.rejected_reason, "target_id ambiguous")
        self.assertEqual(text_target.target_id, "beta_duplicate")
        self.assertFalse(text_target.rejected_reason)
        self.assertIsNone(snap_target)
        self.assertEqual(help_target.source, "text_match")
        self.assertEqual(help_target.target_id, "beta_duplicate")
        self.assertEqual(help_target.rect, (640, 140, 80, 32))
        self.assertFalse(help_target.rejected_reason)

    def test_active_window_context_recovers_foreground_duplicate_action(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate(
                "main_window",
                "Main window",
                "window",
                (20, 80, 360, 280),
                window_title="Main",
                window_rank=1,
            ),
            ControlCandidate(
                "main_save",
                "Save",
                "button",
                (70, 130, 80, 32),
                window_title="Main",
                window_rank=1,
            ),
            ControlCandidate(
                "settings_window",
                "Settings window",
                "window",
                (420, 80, 360, 280),
                window_title="Settings",
                window_rank=0,
            ),
            ControlCandidate(
                "settings_save",
                "Save",
                "button",
                (470, 130, 80, 32),
                window_title="Settings",
                window_rank=0,
            ),
        ]
        instruction = "Click Save in the active window."

        wrong_target = resolve_candidate_target(
            target_id="main_save",
            instruction=instruction,
            candidates=candidates,
            model_rect=(70, 130, 80, 32),
        )
        text_target = resolve_candidate_target(
            target_id="",
            instruction=instruction,
            candidates=candidates,
            model_rect=(70, 130, 80, 32),
        )
        snap_target = snap_candidate_target(
            instruction=instruction,
            candidates=candidates,
            model_rect=(70, 130, 80, 32),
        )
        help_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": instruction,
                    "target_id": "main_save",
                    "target": {"x": 70, "y": 130, "width": 80, "height": 32},
                }
            ),
            self._capture(),
            candidates,
        )

        self.assertEqual(wrong_target.target_id, "main_save")
        self.assertIn(
            wrong_target.rejected_reason,
            {"target_id ambiguous", "target_id semantic mismatch"},
        )
        self.assertEqual(text_target.target_id, "settings_save")
        self.assertFalse(text_target.rejected_reason)
        if snap_target is not None:
            self.assertNotEqual(snap_target.target_id, "main_save")
        self.assertEqual(help_target.source, "text_match")
        self.assertEqual(help_target.target_id, "settings_save")
        self.assertEqual(help_target.rect, (470, 130, 80, 32))
        self.assertFalse(help_target.rejected_reason)

    def test_shorthand_group_context_recovers_requested_duplicate_field(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate("billing_group", "Billing", "group", (20, 60, 420, 70)),
            ControlCandidate("billing_email", "Email", "edit", (150, 80, 220, 32)),
            ControlCandidate("shipping_group", "Shipping", "group", (20, 120, 420, 70)),
            ControlCandidate("shipping_email", "Email", "edit", (150, 130, 220, 32)),
        ]
        instruction = "Enter Billing email."

        wrong_target = resolve_candidate_target(
            target_id="shipping_email",
            instruction=instruction,
            candidates=candidates,
            model_rect=(150, 130, 220, 32),
        )
        text_target = resolve_candidate_target(
            target_id="",
            instruction=instruction,
            candidates=candidates,
            model_rect=(150, 130, 220, 32),
        )
        help_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": instruction,
                    "target_id": "shipping_email",
                    "target": {"x": 150, "y": 130, "width": 220, "height": 32},
                }
            ),
            self._capture(),
            candidates,
        )

        self.assertEqual(wrong_target.target_id, "shipping_email")
        self.assertIn(
            wrong_target.rejected_reason,
            {"target_id ambiguous", "target_id semantic mismatch"},
        )
        self.assertEqual(text_target.target_id, "billing_email")
        self.assertFalse(text_target.rejected_reason)
        self.assertEqual(help_target.source, "text_match")
        self.assertEqual(help_target.target_id, "billing_email")
        self.assertEqual(help_target.rect, (150, 80, 220, 32))
        self.assertFalse(help_target.rejected_reason)

    def test_container_only_menu_request_does_not_recover_arbitrary_child(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate("account_menu", "Account menu", "menu", (20, 80, 500, 80)),
            ControlCandidate("settings", "Settings", "button", (420, 104, 80, 30)),
        ]

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click the Account menu.",
                    "target_id": "account_menu",
                    "target": {"x": 20, "y": 80, "width": 500, "height": 80},
                }
            ),
            self._capture(),
            candidates,
        )

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.target_id, "account_menu")
        self.assertEqual(target.rejected_reason, "target_id control type mismatch")

    def test_surface_action_model_rect_promotes_contained_menu_and_header_buttons(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            (
                "Click Save in the menu.",
                "menu",
                [
                    ControlCandidate("menu", "Account menu", "menu", (20, 80, 500, 80)),
                    ControlCandidate("save", "Save", "button", (420, 104, 60, 30)),
                ],
                "save",
                (420, 104, 60, 30),
            ),
            (
                "Click Sort in the header.",
                "header",
                [
                    ControlCandidate("header", "Name", "headeritem", (10, 10, 200, 40)),
                    ControlCandidate("sort", "Sort", "button", (170, 16, 32, 28)),
                ],
                "sort",
                (170, 16, 32, 28),
            ),
        )
        for instruction, target_id, candidates, expected_id, expected_rect in cases:
            with self.subTest(instruction=instruction):
                broad = next(candidate for candidate in candidates if candidate.id == target_id)
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": target_id,
                            "target": {
                                "x": broad.rect[0],
                                "y": broad.rect[1],
                                "width": broad.rect[2],
                                "height": broad.rect[3],
                            },
                        }
                    ),
                    self._capture(),
                    candidates,
                )

                self.assertEqual(target.source, "candidate_snap")
                self.assertEqual(target.target_id, expected_id)
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, expected_rect)

    def test_surface_context_recovers_to_automation_only_action(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate("main_save", "Save", "button", (100, 100, 70, 30)),
            ControlCandidate("popup", "Settings popup", "window", (420, 80, 300, 120)),
            ControlCandidate(
                "popup_save",
                "",
                "button",
                (630, 160, 32, 32),
                automation_id="save_button",
            ),
        ]

        wrong_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Save in the popup.",
                    "target_id": "main_save",
                    "target": {"x": 100, "y": 100, "width": 70, "height": 30},
                }
            ),
            self._capture(),
            candidates,
        )
        correct_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Save in the popup.",
                    "target_id": "popup_save",
                    "target": {"x": 630, "y": 160, "width": 32, "height": 32},
                }
            ),
            self._capture(),
            candidates,
        )

        self.assertEqual(wrong_target.source, "text_match")
        self.assertEqual(wrong_target.target_id, "popup_save")
        self.assertFalse(wrong_target.rejected_reason)
        self.assertEqual(wrong_target.rect, (630, 160, 32, 32))
        self.assertEqual(correct_target.source, "target_id")
        self.assertEqual(correct_target.target_id, "popup_save")
        self.assertFalse(correct_target.rejected_reason)
        self.assertEqual(correct_target.rect, (630, 160, 32, 32))

    def test_dialog_context_uses_foreground_modal_evidence(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate(
                "page_save",
                "Save",
                "button",
                (100, 100, 70, 30),
                window_title="Editor",
                window_rank=0,
            ),
            ControlCandidate(
                "dialog_save",
                "Save",
                "button",
                (500, 300, 70, 30),
                window_title="Preferences",
                window_rank=1,
            ),
        ]
        instruction = "Click Save in the dialog."
        wrong_target = resolve_candidate_target(
            target_id="page_save",
            instruction=instruction,
            candidates=candidates,
            model_rect=(100, 100, 70, 30),
        )
        text_target = resolve_candidate_target(
            target_id="",
            instruction=instruction,
            candidates=candidates,
            model_rect=(100, 100, 70, 30),
        )
        snap_target = snap_candidate_target(
            instruction=instruction,
            candidates=candidates,
            model_rect=(500, 300, 70, 30),
        )
        help_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": instruction,
                    "target_id": "page_save",
                    "target": {"x": 100, "y": 100, "width": 70, "height": 30},
                }
            ),
            self._capture(),
            candidates,
        )

        self.assertEqual(wrong_target.target_id, "page_save")
        self.assertEqual(wrong_target.rejected_reason, "target_id ambiguous")
        for resolved in (text_target, snap_target, help_target):
            self.assertEqual(resolved.target_id, "dialog_save")
            self.assertFalse(resolved.rejected_reason)
            self.assertEqual(resolved.rect, (500, 300, 70, 30))

    def test_settings_popup_rejects_settings_panel_action(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate("settings_panel", "Settings panel", "pane", (20, 80, 300, 120)),
            ControlCandidate("panel_save", "Save", "button", (230, 160, 60, 30)),
            ControlCandidate("settings_popup", "Settings popup", "window", (420, 80, 300, 120)),
            ControlCandidate("popup_save", "Save", "button", (630, 160, 60, 30)),
        ]
        wrong_target = resolve_candidate_target(
            target_id="panel_save",
            instruction="Click Save in the Settings popup.",
            candidates=candidates,
            model_rect=(630, 160, 60, 30),
        )
        help_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Save in the Settings popup.",
                    "target_id": "panel_save",
                    "target": {"x": 630, "y": 160, "width": 60, "height": 30},
                }
            ),
            self._capture(),
            candidates,
        )
        snap_target = snap_candidate_target(
            instruction="Click Save in the Settings popup.",
            candidates=candidates,
            model_rect=(630, 160, 60, 30),
        )

        self.assertEqual(wrong_target.target_id, "panel_save")
        self.assertIn(
            wrong_target.rejected_reason,
            {"target_id ambiguous", "target_id semantic mismatch"},
        )
        for resolved in (help_target, snap_target):
            self.assertEqual(resolved.target_id, "popup_save")
            self.assertFalse(resolved.rejected_reason)
            self.assertEqual(resolved.rect, (630, 160, 60, 30))

    def test_panel_context_recovers_from_main_pane_action(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate("main", "Main content", "pane", (20, 80, 300, 120)),
            ControlCandidate("main_save", "Save", "button", (230, 160, 60, 30)),
            ControlCandidate("settings_panel", "Settings panel", "pane", (420, 80, 300, 120)),
            ControlCandidate("panel_save", "Save", "button", (630, 160, 60, 30)),
        ]
        instruction = "Click Save in the panel."

        wrong_target = resolve_candidate_target(
            target_id="main_save",
            instruction=instruction,
            candidates=candidates,
            model_rect=(230, 160, 60, 30),
        )
        text_target = resolve_candidate_target(
            target_id="",
            instruction=instruction,
            candidates=candidates,
            model_rect=(230, 160, 60, 30),
        )
        wrong_snap = snap_candidate_target(
            instruction=instruction,
            candidates=candidates,
            model_rect=(230, 160, 60, 30),
        )
        help_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": instruction,
                    "target_id": "main_save",
                    "target": {"x": 230, "y": 160, "width": 60, "height": 30},
                }
            ),
            self._capture(),
            candidates,
        )

        self.assertEqual(wrong_target.target_id, "main_save")
        self.assertEqual(wrong_target.rejected_reason, "target_id ambiguous")
        self.assertIsNone(wrong_snap)
        for resolved in (text_target, help_target):
            self.assertEqual(resolved.target_id, "panel_save")
            self.assertFalse(resolved.rejected_reason)
            self.assertEqual(resolved.rect, (630, 160, 60, 30))

    def test_generic_pane_context_with_duplicate_actions_stays_ambiguous(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate("main", "Main content", "pane", (20, 80, 300, 120)),
            ControlCandidate("main_save", "Save", "button", (230, 160, 60, 30)),
            ControlCandidate("settings_pane", "Settings pane", "pane", (420, 80, 300, 120)),
            ControlCandidate("pane_save", "Save", "button", (630, 160, 60, 30)),
        ]
        instruction = "Click Save in the pane."

        wrong_target = resolve_candidate_target(
            target_id="main_save",
            instruction=instruction,
            candidates=candidates,
            model_rect=(230, 160, 60, 30),
        )
        text_target = resolve_candidate_target(
            target_id="",
            instruction=instruction,
            candidates=candidates,
            model_rect=(230, 160, 60, 30),
        )
        wrong_snap = snap_candidate_target(
            instruction=instruction,
            candidates=candidates,
            model_rect=(230, 160, 60, 30),
        )
        help_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": instruction,
                    "target_id": "main_save",
                    "target": {"x": 230, "y": 160, "width": 60, "height": 30},
                }
            ),
            self._capture(),
            candidates,
        )

        self.assertEqual(wrong_target.target_id, "main_save")
        self.assertEqual(wrong_target.rejected_reason, "target_id ambiguous")
        self.assertEqual(text_target.target_id, "main_save")
        self.assertEqual(text_target.rejected_reason, "ambiguous text match")
        self.assertIsNone(wrong_snap)
        self.assertEqual(help_target.target_id, "main_save")
        self.assertEqual(help_target.rejected_reason, "target_id ambiguous")

    def test_notification_dismiss_recovers_from_page_dismiss_action(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate("main_dismiss", "Dismiss", "button", (230, 160, 70, 30)),
            ControlCandidate("updates_notification", "Updates notification", "pane", (420, 80, 300, 120)),
            ControlCandidate("notification_dismiss", "Dismiss", "button", (630, 160, 70, 30)),
        ]
        instruction = "Click Dismiss in the Updates notification."
        wrong_target = resolve_candidate_target(
            target_id="main_dismiss",
            instruction=instruction,
            candidates=candidates,
            model_rect=(630, 160, 70, 30),
        )
        text_target = resolve_candidate_target(
            target_id="",
            instruction=instruction,
            candidates=candidates,
            model_rect=(630, 160, 70, 30),
        )
        snap_target = snap_candidate_target(
            instruction=instruction,
            candidates=candidates,
            model_rect=(630, 160, 70, 30),
        )
        help_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": instruction,
                    "target_id": "main_dismiss",
                    "target": {"x": 630, "y": 160, "width": 70, "height": 30},
                }
            ),
            self._capture(),
            candidates,
        )

        self.assertEqual(wrong_target.target_id, "main_dismiss")
        self.assertEqual(wrong_target.rejected_reason, "target_id semantic mismatch")
        for resolved in (text_target, snap_target, help_target):
            self.assertEqual(resolved.target_id, "notification_dismiss")
            self.assertFalse(resolved.rejected_reason)
            self.assertEqual(resolved.rect, (630, 160, 70, 30))

    def test_generic_popup_dismiss_recovers_from_page_dismiss_action(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate("main", "Main content", "pane", (20, 80, 300, 120)),
            ControlCandidate("main_dismiss", "Dismiss", "button", (230, 160, 70, 30)),
            ControlCandidate("settings_popup", "Settings popup", "window", (420, 80, 300, 120)),
            ControlCandidate("popup_dismiss", "Dismiss", "button", (630, 160, 70, 30)),
        ]
        instruction = "Click Dismiss in the popup."

        wrong_target = resolve_candidate_target(
            target_id="main_dismiss",
            instruction=instruction,
            candidates=candidates,
            model_rect=(230, 160, 70, 30),
        )
        text_target = resolve_candidate_target(
            target_id="",
            instruction=instruction,
            candidates=candidates,
            model_rect=(230, 160, 70, 30),
        )
        wrong_snap = snap_candidate_target(
            instruction=instruction,
            candidates=candidates,
            model_rect=(230, 160, 70, 30),
        )
        help_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": instruction,
                    "target_id": "main_dismiss",
                    "target": {"x": 230, "y": 160, "width": 70, "height": 30},
                }
            ),
            self._capture(),
            candidates,
        )

        self.assertEqual(wrong_target.target_id, "main_dismiss")
        self.assertEqual(wrong_target.rejected_reason, "target_id semantic mismatch")
        if wrong_snap is not None:
            self.assertEqual(wrong_snap.target_id, "main_dismiss")
            self.assertTrue(wrong_snap.rejected_reason)
        for resolved in (text_target, help_target):
            self.assertEqual(resolved.target_id, "popup_dismiss")
            self.assertFalse(resolved.rejected_reason)
            self.assertEqual(resolved.rect, (630, 160, 70, 30))

    def test_positional_duplicate_action_recovers_requested_button(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate("edit1", "", "button", (100, 100, 32, 32), automation_id="editButton"),
            ControlCandidate("edit2", "", "button", (150, 100, 32, 32), automation_id="editButton"),
        ]
        instruction = "Click the second edit button."

        wrong_target = resolve_candidate_target(
            target_id="edit1",
            instruction=instruction,
            candidates=candidates,
            model_rect=(100, 100, 32, 32),
        )
        correct_target = resolve_candidate_target(
            target_id="edit2",
            instruction=instruction,
            candidates=candidates,
            model_rect=(150, 100, 32, 32),
        )
        text_target = resolve_candidate_target(
            target_id="",
            instruction=instruction,
            candidates=candidates,
            model_rect=(100, 100, 32, 32),
        )
        snap_target = snap_candidate_target(
            instruction=instruction,
            candidates=candidates,
            model_rect=(100, 100, 32, 32),
        )
        help_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": instruction,
                    "target_id": "edit1",
                    "target": {"x": 100, "y": 100, "width": 32, "height": 32},
                }
            ),
            self._capture(),
            candidates,
        )

        self.assertEqual(wrong_target.target_id, "edit1")
        self.assertEqual(wrong_target.rejected_reason, "target_id semantic mismatch")
        self.assertEqual(correct_target.target_id, "edit2")
        self.assertFalse(correct_target.rejected_reason)
        self.assertEqual(text_target.target_id, "edit2")
        self.assertFalse(text_target.rejected_reason)
        self.assertEqual(snap_target.target_id, "edit2")
        self.assertFalse(snap_target.rejected_reason)
        self.assertEqual(help_target.source, "text_match")
        self.assertEqual(help_target.target_id, "edit2")
        self.assertFalse(help_target.rejected_reason)
        self.assertEqual(help_target.rect, (150, 100, 32, 32))

    def test_shorthand_positional_row_context_recovers_requested_action(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate("row_top", "Top", "listitem", (80, 90, 240, 50)),
            ControlCandidate("archive_top", "Archive", "button", (240, 100, 80, 30)),
            ControlCandidate("row_bottom", "Bottom", "listitem", (80, 140, 240, 50)),
            ControlCandidate("archive_bottom", "Archive", "button", (240, 150, 80, 30)),
        ]
        instruction = "Archive Bottom."

        wrong_target = resolve_candidate_target(
            target_id="archive_top",
            instruction=instruction,
            candidates=candidates,
            model_rect=(240, 100, 80, 30),
        )
        text_target = resolve_candidate_target(
            target_id="",
            instruction=instruction,
            candidates=candidates,
            model_rect=(240, 100, 80, 30),
        )
        snap_target = snap_candidate_target(
            instruction=instruction,
            candidates=candidates,
            model_rect=(240, 100, 80, 30),
        )
        help_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": instruction,
                    "target_id": "archive_top",
                    "target": {"x": 240, "y": 100, "width": 80, "height": 30},
                }
            ),
            self._capture(),
            candidates,
        )

        self.assertEqual(wrong_target.target_id, "archive_top")
        self.assertEqual(wrong_target.rejected_reason, "target_id ambiguous")
        for resolved in (text_target, snap_target, help_target):
            self.assertEqual(resolved.target_id, "archive_bottom")
            self.assertFalse(resolved.rejected_reason)
            self.assertEqual(resolved.rect, (240, 150, 80, 30))

    def test_shorthand_dataitem_row_context_recovers_requested_action(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate("row_alice", "Alice", "dataitem", (20, 80, 760, 48)),
            ControlCandidate("delete_alice", "Delete", "button", (540, 88, 90, 32)),
            ControlCandidate("row_bob", "Bob", "dataitem", (20, 140, 760, 48)),
            ControlCandidate("delete_bob", "Delete", "button", (540, 148, 90, 32)),
        ]
        instruction = "Delete Bob."

        wrong_target = resolve_candidate_target(
            target_id="delete_alice",
            instruction=instruction,
            candidates=candidates,
            model_rect=(540, 88, 90, 32),
        )
        text_target = resolve_candidate_target(
            target_id="",
            instruction=instruction,
            candidates=candidates,
            model_rect=(540, 88, 90, 32),
        )
        snap_target = snap_candidate_target(
            instruction=instruction,
            candidates=candidates,
            model_rect=(540, 88, 90, 32),
        )
        help_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": instruction,
                    "target_id": "delete_alice",
                    "target": {"x": 540, "y": 88, "width": 90, "height": 32},
                }
            ),
            self._capture(),
            candidates,
        )

        self.assertEqual(wrong_target.target_id, "delete_alice")
        self.assertEqual(wrong_target.rejected_reason, "target_id semantic mismatch")
        for resolved in (text_target, snap_target, help_target):
            self.assertEqual(resolved.target_id, "delete_bob")
            self.assertFalse(resolved.rejected_reason)
            self.assertEqual(resolved.rect, (540, 148, 90, 32))

    def test_shorthand_card_context_recovers_adjacent_requested_action(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate("alpha_card", "Alpha card", "listitem", (20, 80, 500, 48)),
            ControlCandidate("alpha_save", "Save", "button", (540, 88, 70, 32)),
            ControlCandidate("beta_card", "Beta card", "listitem", (20, 140, 500, 48)),
            ControlCandidate("beta_save", "Save", "button", (540, 148, 70, 32)),
        ]
        instruction = "Click Beta Save."

        wrong_target = resolve_candidate_target(
            target_id="alpha_save",
            instruction=instruction,
            candidates=candidates,
            model_rect=(540, 88, 70, 32),
        )
        text_target = resolve_candidate_target(
            target_id="",
            instruction=instruction,
            candidates=candidates,
            model_rect=(540, 88, 70, 32),
        )
        snap_target = snap_candidate_target(
            instruction=instruction,
            candidates=candidates,
            model_rect=(540, 88, 70, 32),
        )
        help_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": instruction,
                    "target_id": "alpha_save",
                    "target": {"x": 540, "y": 88, "width": 70, "height": 32},
                }
            ),
            self._capture(),
            candidates,
        )

        self.assertEqual(wrong_target.target_id, "alpha_save")
        self.assertEqual(wrong_target.rejected_reason, "target_id semantic mismatch")
        for resolved in (text_target, snap_target, help_target):
            self.assertEqual(resolved.target_id, "beta_save")
            self.assertFalse(resolved.rejected_reason)
            self.assertEqual(resolved.rect, (540, 148, 70, 32))

    def test_positional_duplicate_controls_recover_requested_field_and_option(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from help_session import resolve_help_target

        cases = (
            (
                "Type in the second field.",
                [
                    ControlCandidate("email", "Email", "edit", (100, 100, 220, 32)),
                    ControlCandidate("phone", "Phone", "edit", (100, 150, 220, 32)),
                ],
                "email",
                "phone",
                (100, 100, 220, 32),
                (100, 150, 220, 32),
            ),
            (
                "Select the second option.",
                [
                    ControlCandidate("first", "Option", "radiobutton", (100, 100, 120, 32)),
                    ControlCandidate("second", "Option", "radiobutton", (100, 150, 120, 32)),
                ],
                "first",
                "second",
                (100, 100, 120, 32),
                (100, 150, 120, 32),
            ),
            (
                "Click the second search result.",
                [
                    ControlCandidate("result1", "Search result", "listitem", (100, 100, 300, 40)),
                    ControlCandidate("result2", "Search result", "listitem", (100, 150, 300, 40)),
                ],
                "result1",
                "result2",
                (100, 100, 300, 40),
                (100, 150, 300, 40),
            ),
        )

        for instruction, candidates, wrong_id, expected_id, wrong_rect, expected_rect in cases:
            with self.subTest(instruction=instruction):
                wrong_target = resolve_candidate_target(
                    target_id=wrong_id,
                    instruction=instruction,
                    candidates=candidates,
                    model_rect=wrong_rect,
                )
                text_target = resolve_candidate_target(
                    target_id="",
                    instruction=instruction,
                    candidates=candidates,
                    model_rect=wrong_rect,
                )
                snap_target = snap_candidate_target(
                    instruction=instruction,
                    candidates=candidates,
                    model_rect=wrong_rect,
                )
                help_target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": wrong_id,
                            "target": {
                                "x": wrong_rect[0],
                                "y": wrong_rect[1],
                                "width": wrong_rect[2],
                                "height": wrong_rect[3],
                            },
                        }
                    ),
                    self._capture(),
                    candidates,
                )

                self.assertEqual(wrong_target.target_id, wrong_id)
                self.assertEqual(wrong_target.rejected_reason, "target_id semantic mismatch")
                self.assertEqual(text_target.target_id, expected_id)
                self.assertFalse(text_target.rejected_reason)
                self.assertEqual(snap_target.target_id, expected_id)
                self.assertFalse(snap_target.rejected_reason)
                self.assertEqual(help_target.source, "text_match")
                self.assertEqual(help_target.target_id, expected_id)
                self.assertEqual(help_target.rect, expected_rect)
                self.assertFalse(help_target.rejected_reason)

    def test_spelled_out_higher_ordinal_recovers_requested_duplicate_control(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate("c1", "Option", "checkbox", (100, 100, 120, 32)),
            ControlCandidate("c2", "Option", "checkbox", (100, 140, 120, 32)),
            ControlCandidate("c3", "Option", "checkbox", (100, 180, 120, 32)),
            ControlCandidate("c4", "Option", "checkbox", (100, 220, 120, 32)),
        ]
        wrong_rect = (100, 100, 120, 32)
        instruction = "Check the fourth checkbox."

        wrong_target = resolve_candidate_target(
            target_id="c1",
            instruction=instruction,
            candidates=candidates,
            model_rect=wrong_rect,
        )
        text_target = resolve_candidate_target(
            target_id="",
            instruction=instruction,
            candidates=candidates,
            model_rect=wrong_rect,
        )
        help_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": instruction,
                    "target_id": "c1",
                    "target": {
                        "x": wrong_rect[0],
                        "y": wrong_rect[1],
                        "width": wrong_rect[2],
                        "height": wrong_rect[3],
                    },
                }
            ),
            self._capture(),
            candidates,
        )

        self.assertEqual(wrong_target.target_id, "c1")
        self.assertEqual(wrong_target.rejected_reason, "target_id semantic mismatch")
        self.assertEqual(text_target.source, "text_match")
        self.assertEqual(text_target.target_id, "c4")
        self.assertFalse(text_target.rejected_reason)
        self.assertEqual(help_target.source, "text_match")
        self.assertEqual(help_target.target_id, "c4")
        self.assertEqual(help_target.rect, (100, 220, 120, 32))
        self.assertFalse(help_target.rejected_reason)

    def test_same_label_modal_button_uses_geometry_over_foreground_rank(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click the Save button in the modal.",
                    "target": {"x": 360, "y": 200, "width": 260, "height": 120},
                }
            ),
            self._capture(),
            [
                ControlCandidate(
                    "bg",
                    "Save",
                    "button",
                    (100, 100, 80, 32),
                    window_title="Editor",
                    window_rank=0,
                ),
                ControlCandidate(
                    "modal",
                    "Save",
                    "button",
                    (400, 240, 80, 32),
                    window_title="Save changes",
                    window_rank=1,
                ),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "modal")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (400, 240, 80, 32))

    def test_same_label_dialog_button_uses_context_over_wrong_geometry(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click OK in the dialog.",
                    "target_id": "dialog",
                    "target": {"x": 100, "y": 100, "width": 80, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate(
                    "bg",
                    "OK",
                    "button",
                    (100, 100, 80, 32),
                    window_title="Settings",
                    window_rank=0,
                ),
                ControlCandidate(
                    "dialog",
                    "OK",
                    "button",
                    (400, 240, 80, 32),
                    window_title="Preferences dialog",
                    window_rank=1,
                ),
            ],
        )

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.target_id, "dialog")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (400, 240, 80, 32))

    def test_launcher_wording_rejects_same_label_menuitem_geometry(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Settings launcher.",
                    "target_id": "launcher",
                    "target": {"x": 200, "y": 80, "width": 160, "height": 28},
                }
            ),
            self._capture(),
            [
                ControlCandidate("launcher", "Settings", "button", (20, 20, 100, 32), window_title="Start"),
                ControlCandidate("item", "Settings", "menuitem", (200, 80, 160, 28), window_title="Settings menu"),
            ],
        )

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.target_id, "launcher")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (20, 20, 100, 32))

    def test_selector_wrong_target_id_recovers_to_combobox(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open the Country selector.",
                    "target_id": "c002",
                    "target": {"x": 400, "y": 160, "width": 100, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Country", "combobox", (120, 160, 220, 32)),
                ControlCandidate("c002", "Country", "button", (400, 160, 100, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (120, 160, 220, 32))

    def test_explicit_combo_box_rejects_same_label_edit_field(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate("settings_edit", "Settings", "edit", (100, 100, 180, 32)),
            ControlCandidate("settings_combo", "Settings", "combobox", (100, 150, 180, 32)),
        ]
        instruction = "Click Settings combo box."

        wrong_target = resolve_candidate_target(
            target_id="settings_edit",
            instruction=instruction,
            candidates=candidates,
            model_rect=(100, 100, 180, 32),
        )
        text_target = resolve_candidate_target(
            target_id="",
            instruction=instruction,
            candidates=candidates,
            model_rect=(100, 100, 180, 32),
        )
        snap_target = snap_candidate_target(
            instruction=instruction,
            candidates=candidates,
            model_rect=(100, 100, 180, 32),
        )
        help_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": instruction,
                    "target_id": "settings_edit",
                    "target": {"x": 100, "y": 100, "width": 180, "height": 32},
                }
            ),
            self._capture(),
            candidates,
        )

        self.assertEqual(wrong_target.target_id, "settings_edit")
        self.assertEqual(wrong_target.rejected_reason, "target_id control type mismatch")
        for resolved in (text_target, snap_target, help_target):
            self.assertEqual(resolved.target_id, "settings_combo")
            self.assertFalse(resolved.rejected_reason)
            self.assertEqual(resolved.rect, (100, 150, 180, 32))

    def test_explicit_dropdown_rejects_same_label_button_for_combobox(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate("settings_button", "Settings", "button", (100, 100, 180, 32)),
            ControlCandidate("settings_combo", "Settings", "combobox", (100, 150, 180, 32)),
        ]
        instruction = "Click Settings dropdown."

        wrong_target = resolve_candidate_target(
            target_id="settings_button",
            instruction=instruction,
            candidates=candidates,
            model_rect=(100, 100, 180, 32),
        )
        text_target = resolve_candidate_target(
            target_id="",
            instruction=instruction,
            candidates=candidates,
            model_rect=(100, 100, 180, 32),
        )
        snap_target = snap_candidate_target(
            instruction=instruction,
            candidates=candidates,
            model_rect=(100, 100, 180, 32),
        )
        help_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": instruction,
                    "target_id": "settings_button",
                    "target": {"x": 100, "y": 100, "width": 180, "height": 32},
                }
            ),
            self._capture(),
            candidates,
        )

        self.assertEqual(wrong_target.target_id, "settings_button")
        self.assertEqual(wrong_target.rejected_reason, "target_id control type mismatch")
        self.assertEqual(snap_target.target_id, "settings_button")
        self.assertEqual(snap_target.rejected_reason, "candidate semantic mismatch")
        for resolved in (text_target, help_target):
            self.assertEqual(resolved.target_id, "settings_combo")
            self.assertFalse(resolved.rejected_reason)
            self.assertEqual(resolved.rect, (100, 150, 180, 32))

    def test_explicit_spin_box_rejects_same_label_edit_field(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate("quantity_edit", "Quantity", "edit", (100, 100, 180, 32)),
            ControlCandidate("quantity_spinner", "Quantity", "spinner", (100, 150, 180, 32)),
        ]
        instruction = "Use Quantity spin box."

        wrong_target = resolve_candidate_target(
            target_id="quantity_edit",
            instruction=instruction,
            candidates=candidates,
            model_rect=(100, 100, 180, 32),
        )
        text_target = resolve_candidate_target(
            target_id="",
            instruction=instruction,
            candidates=candidates,
            model_rect=(100, 100, 180, 32),
        )
        snap_target = snap_candidate_target(
            instruction=instruction,
            candidates=candidates,
            model_rect=(100, 100, 180, 32),
        )
        help_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": instruction,
                    "target_id": "quantity_edit",
                    "target": {"x": 100, "y": 100, "width": 180, "height": 32},
                }
            ),
            self._capture(),
            candidates,
        )

        self.assertEqual(wrong_target.target_id, "quantity_edit")
        self.assertEqual(wrong_target.rejected_reason, "target_id control type mismatch")
        for resolved in (text_target, snap_target, help_target):
            self.assertEqual(resolved.target_id, "quantity_spinner")
            self.assertFalse(resolved.rejected_reason)
            self.assertEqual(resolved.rect, (100, 150, 180, 32))

    def test_explicit_slider_rejects_same_label_list_item(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate("settings_item", "Settings", "listitem", (10, 10, 120, 32)),
            ControlCandidate("settings_slider", "Settings", "slider", (10, 60, 120, 32)),
        ]
        instruction = "Select Settings slider."

        wrong_target = resolve_candidate_target(
            target_id="settings_item",
            instruction=instruction,
            candidates=candidates,
            model_rect=(10, 10, 120, 32),
        )
        text_target = resolve_candidate_target(
            target_id="",
            instruction=instruction,
            candidates=candidates,
            model_rect=(10, 10, 120, 32),
        )
        snap_target = snap_candidate_target(
            instruction=instruction,
            candidates=candidates,
            model_rect=(10, 10, 120, 32),
        )
        help_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": instruction,
                    "target_id": "settings_item",
                    "target": {"x": 10, "y": 10, "width": 120, "height": 32},
                }
            ),
            self._capture(),
            candidates,
        )

        self.assertEqual(wrong_target.target_id, "settings_item")
        self.assertEqual(wrong_target.rejected_reason, "target_id control type mismatch")
        for resolved in (text_target, snap_target, help_target):
            self.assertEqual(resolved.target_id, "settings_slider")
            self.assertFalse(resolved.rejected_reason)
            self.assertEqual(resolved.rect, (10, 60, 120, 32))

    def test_explicit_pane_wording_rejects_same_label_button(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from help_session import resolve_help_target

        cases = (
            ("Click Settings pane.", "Settings pane"),
            ("Click Settings panel.", "Settings panel"),
        )
        for instruction, pane_text in cases:
            with self.subTest(instruction=instruction):
                candidates = [
                    ControlCandidate("settings_button", "Settings", "button", (10, 10, 120, 32)),
                    ControlCandidate("settings_pane", pane_text, "pane", (10, 60, 220, 120)),
                ]

                wrong_target = resolve_candidate_target(
                    target_id="settings_button",
                    instruction=instruction,
                    candidates=candidates,
                    model_rect=(10, 10, 120, 32),
                )
                text_target = resolve_candidate_target(
                    target_id="",
                    instruction=instruction,
                    candidates=candidates,
                    model_rect=(10, 10, 120, 32),
                )
                snap_target = snap_candidate_target(
                    instruction=instruction,
                    candidates=candidates,
                    model_rect=(10, 10, 120, 32),
                )
                help_target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "settings_button",
                            "target": {"x": 10, "y": 10, "width": 120, "height": 32},
                        }
                    ),
                    self._capture(),
                    candidates,
                )

                self.assertEqual(wrong_target.target_id, "settings_button")
                self.assertEqual(wrong_target.rejected_reason, "target_id control type mismatch")
                self.assertIsNone(snap_target)
                for resolved in (text_target, help_target):
                    self.assertEqual(resolved.target_id, "settings_pane")
                    self.assertFalse(resolved.rejected_reason)
                    self.assertEqual(resolved.rect, (10, 60, 220, 120))

    def test_select_button_rejects_same_label_radio_button(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate("settings_radio", "Settings", "radiobutton", (10, 10, 100, 30)),
            ControlCandidate("settings_button", "Settings", "button", (10, 60, 100, 30)),
        ]
        instruction = "Select the Settings button."

        wrong_target = resolve_candidate_target(
            target_id="settings_radio",
            instruction=instruction,
            candidates=candidates,
            model_rect=(10, 10, 100, 30),
        )
        correct_target = resolve_candidate_target(
            target_id="settings_button",
            instruction=instruction,
            candidates=candidates,
            model_rect=(10, 10, 100, 30),
        )
        text_target = resolve_candidate_target(
            target_id="",
            instruction=instruction,
            candidates=candidates,
            model_rect=(10, 10, 100, 30),
        )
        snap_target = snap_candidate_target(
            instruction=instruction,
            candidates=candidates,
            model_rect=(10, 10, 100, 30),
        )
        help_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": instruction,
                    "target_id": "settings_radio",
                    "target": {"x": 10, "y": 10, "width": 100, "height": 30},
                }
            ),
            self._capture(),
            candidates,
        )

        self.assertEqual(wrong_target.target_id, "settings_radio")
        self.assertEqual(wrong_target.rejected_reason, "target_id control type mismatch")
        self.assertEqual(correct_target.target_id, "settings_button")
        self.assertFalse(correct_target.rejected_reason)
        for resolved in (text_target, snap_target, help_target):
            self.assertEqual(resolved.target_id, "settings_button")
            self.assertFalse(resolved.rejected_reason)
            self.assertEqual(resolved.rect, (10, 60, 100, 30))

    def test_dropdown_launcher_rejects_same_label_menuitem_option(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate("combo", "Country", "combobox", (10, 10, 180, 32)),
            ControlCandidate("option", "Country", "menuitem", (10, 60, 180, 28)),
        ]
        instruction = "Open the Country dropdown."

        wrong_target = resolve_candidate_target(
            target_id="option",
            instruction=instruction,
            candidates=candidates,
            model_rect=(10, 60, 180, 28),
        )
        text_target = resolve_candidate_target(
            target_id="",
            instruction=instruction,
            candidates=candidates,
            model_rect=(10, 60, 180, 28),
        )
        wrong_snap = snap_candidate_target(
            instruction=instruction,
            candidates=candidates,
            model_rect=(10, 60, 180, 28),
        )
        help_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": instruction,
                    "target_id": "option",
                    "target": {"x": 10, "y": 60, "width": 180, "height": 28},
                }
            ),
            self._capture(),
            candidates,
        )

        self.assertEqual(wrong_target.target_id, "option")
        self.assertEqual(wrong_target.rejected_reason, "target_id control type mismatch")
        self.assertEqual(text_target.target_id, "combo")
        self.assertFalse(text_target.rejected_reason)
        if wrong_snap is not None:
            self.assertNotEqual(wrong_snap.target_id, "option")
        self.assertEqual(help_target.source, "text_match")
        self.assertEqual(help_target.target_id, "combo")
        self.assertFalse(help_target.rejected_reason)
        self.assertEqual(help_target.rect, (10, 10, 180, 32))

    def test_dropdown_item_request_recovers_visible_menuitem_from_launcher_id(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate("combo", "Status", "combobox", (10, 10, 180, 32)),
            ControlCandidate("active", "Active", "menuitem", (10, 46, 180, 28)),
        ]
        instruction = "Click Active in the dropdown."

        wrong_target = resolve_candidate_target(
            target_id="combo",
            instruction=instruction,
            candidates=candidates,
            model_rect=(10, 46, 180, 28),
        )
        correct_target = resolve_candidate_target(
            target_id="active",
            instruction=instruction,
            candidates=candidates,
            model_rect=(10, 46, 180, 28),
        )
        text_target = resolve_candidate_target(
            target_id="",
            instruction=instruction,
            candidates=candidates,
            model_rect=(10, 46, 180, 28),
        )
        snap_target = snap_candidate_target(
            instruction=instruction,
            candidates=candidates,
            model_rect=(10, 46, 180, 28),
        )
        help_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": instruction,
                    "target_id": "combo",
                    "target": {"x": 10, "y": 46, "width": 180, "height": 28},
                }
            ),
            self._capture(),
            candidates,
        )

        self.assertEqual(wrong_target.target_id, "combo")
        self.assertEqual(wrong_target.rejected_reason, "target_id control type mismatch")
        self.assertEqual(correct_target.target_id, "active")
        self.assertFalse(correct_target.rejected_reason)
        for resolved in (text_target, snap_target, help_target):
            self.assertEqual(resolved.target_id, "active")
            self.assertFalse(resolved.rejected_reason)
            self.assertEqual(resolved.rect, (10, 46, 180, 28))

    def test_dropdown_launcher_rejects_same_label_button_menuitem(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate("launcher", "Account", "button", (20, 20, 120, 32)),
            ControlCandidate(
                "stale",
                "Account",
                "menuitem",
                (200, 80, 160, 28),
                window_title="Account menu",
            ),
        ]
        instruction = "Open the account dropdown."

        wrong_target = resolve_candidate_target(
            target_id="stale",
            instruction=instruction,
            candidates=candidates,
            model_rect=(200, 80, 160, 28),
        )
        text_target = resolve_candidate_target(
            target_id="",
            instruction=instruction,
            candidates=candidates,
            model_rect=(200, 80, 160, 28),
        )
        wrong_snap = snap_candidate_target(
            instruction=instruction,
            candidates=candidates,
            model_rect=(200, 80, 160, 28),
        )
        help_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": instruction,
                    "target_id": "stale",
                    "target": {"x": 200, "y": 80, "width": 160, "height": 28},
                }
            ),
            self._capture(),
            candidates,
        )

        self.assertEqual(wrong_target.target_id, "stale")
        self.assertEqual(wrong_target.rejected_reason, "target_id control type mismatch")
        self.assertEqual(text_target.target_id, "launcher")
        self.assertFalse(text_target.rejected_reason)
        if wrong_snap is not None:
            self.assertNotEqual(wrong_snap.target_id, "stale")
        self.assertEqual(help_target.source, "text_match")
        self.assertEqual(help_target.target_id, "launcher")
        self.assertFalse(help_target.rejected_reason)
        self.assertEqual(help_target.rect, (20, 20, 120, 32))

    def test_drop_down_phrase_does_not_match_literal_drop_button(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate("launcher", "Export", "splitbutton", (120, 80, 120, 32)),
            ControlCandidate("drop_literal", "Drop", "button", (300, 80, 80, 32)),
        ]
        instruction = "Open Export drop down."

        wrong_target = resolve_candidate_target(
            target_id="drop_literal",
            instruction=instruction,
            candidates=candidates,
            model_rect=(300, 80, 80, 32),
        )
        text_target = resolve_candidate_target(
            target_id="",
            instruction=instruction,
            candidates=candidates,
            model_rect=(300, 80, 80, 32),
        )
        snap_target = snap_candidate_target(
            instruction=instruction,
            candidates=candidates,
            model_rect=(300, 80, 80, 32),
        )
        help_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": instruction,
                    "target_id": "drop_literal",
                    "target": {"x": 300, "y": 80, "width": 80, "height": 32},
                }
            ),
            self._capture(),
            candidates,
        )

        self.assertEqual(wrong_target.target_id, "drop_literal")
        self.assertEqual(wrong_target.rejected_reason, "target_id control type mismatch")
        self.assertIsNone(text_target)
        self.assertIsNone(snap_target)
        self.assertEqual(help_target.target_id, "drop_literal")
        self.assertEqual(help_target.rejected_reason, "target_id control type mismatch")

    def test_singleton_literal_item_recovers_from_stale_action_geometry(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate("stale", "Archive", "button", (320, 100, 90, 32)),
            ControlCandidate("item", "Item", "button", (100, 100, 90, 32)),
        ]
        instruction = "Click Item."

        wrong_target = resolve_candidate_target(
            target_id="stale",
            instruction=instruction,
            candidates=candidates,
            model_rect=(320, 100, 90, 32),
        )
        text_target = resolve_candidate_target(
            target_id="",
            instruction=instruction,
            candidates=candidates,
            model_rect=(320, 100, 90, 32),
        )
        snap_target = snap_candidate_target(
            instruction=instruction,
            candidates=candidates,
            model_rect=(320, 100, 90, 32),
        )
        help_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": instruction,
                    "target_id": "stale",
                    "target": {"x": 320, "y": 100, "width": 90, "height": 32},
                }
            ),
            self._capture(),
            candidates,
        )

        self.assertEqual(wrong_target.target_id, "stale")
        self.assertEqual(wrong_target.rejected_reason, "target_id semantic mismatch")
        self.assertEqual(text_target.source, "text_match")
        self.assertEqual(text_target.target_id, "item")
        self.assertFalse(text_target.rejected_reason)
        if snap_target is not None:
            self.assertNotEqual(snap_target.target_id, "stale")
        self.assertEqual(help_target.source, "text_match")
        self.assertEqual(help_target.target_id, "item")
        self.assertFalse(help_target.rejected_reason)
        self.assertEqual(help_target.rect, (100, 100, 90, 32))

    def test_textbox_wrong_target_id_rejects_same_label_combobox(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target, snap_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate("edit", "Email", "edit", (100, 100, 220, 32)),
            ControlCandidate("combo", "Email", "combobox", (100, 150, 220, 32)),
        ]
        instruction = "Type in the Email textbox."

        wrong_target = resolve_candidate_target(
            target_id="combo",
            instruction=instruction,
            candidates=candidates,
            model_rect=(100, 150, 220, 32),
        )
        text_target = resolve_candidate_target(
            target_id="",
            instruction=instruction,
            candidates=candidates,
            model_rect=(100, 150, 220, 32),
        )
        snap_target = snap_candidate_target(
            instruction=instruction,
            candidates=candidates,
            model_rect=(100, 150, 220, 32),
        )
        help_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": instruction,
                    "target_id": "combo",
                    "target": {"x": 100, "y": 150, "width": 220, "height": 32},
                }
            ),
            self._capture(),
            candidates,
        )

        self.assertEqual(wrong_target.target_id, "combo")
        self.assertEqual(wrong_target.rejected_reason, "target_id control type mismatch")
        self.assertEqual(text_target.source, "text_match")
        self.assertEqual(text_target.target_id, "edit")
        self.assertFalse(text_target.rejected_reason)
        self.assertEqual(snap_target.target_id, "edit")
        self.assertFalse(snap_target.rejected_reason)
        self.assertEqual(help_target.source, "text_match")
        self.assertEqual(help_target.target_id, "edit")
        self.assertFalse(help_target.rejected_reason)
        self.assertEqual(help_target.rect, (100, 100, 220, 32))

    def test_generic_dropdown_launcher_recovers_from_open_option_target_id(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate("combo", "Country", "combobox", (10, 10, 160, 32)),
            ControlCandidate("option", "Canada", "menuitem", (10, 44, 160, 28)),
        ]
        instruction = "Open this dropdown."

        wrong_target = resolve_candidate_target(
            target_id="option",
            instruction=instruction,
            candidates=candidates,
            model_rect=(10, 44, 160, 28),
        )
        text_target = resolve_candidate_target(
            target_id="",
            instruction=instruction,
            candidates=candidates,
            model_rect=(10, 44, 160, 28),
        )
        help_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": instruction,
                    "target_id": "option",
                    "target": {"x": 10, "y": 44, "width": 160, "height": 28},
                }
            ),
            self._capture(),
            candidates,
        )

        self.assertEqual(wrong_target.target_id, "option")
        self.assertEqual(wrong_target.rejected_reason, "target_id control type mismatch")
        self.assertEqual(text_target.source, "text_match")
        self.assertEqual(text_target.target_id, "combo")
        self.assertFalse(text_target.rejected_reason)
        self.assertEqual(help_target.source, "text_match")
        self.assertEqual(help_target.target_id, "combo")
        self.assertFalse(help_target.rejected_reason)
        self.assertEqual(help_target.rect, (10, 10, 160, 32))

    def test_selector_model_rect_highlights_combobox(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click the Country picker.",
                    "target": {"x": 400, "y": 160, "width": 100, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Country", "combobox", (120, 160, 220, 32)),
                ControlCandidate("c002", "Country", "button", (400, 160, 100, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (120, 160, 220, 32))

    def test_contextual_picker_model_rect_highlights_launcher_button(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open the Date picker.",
                    "target": {"x": 120, "y": 160, "width": 120, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Date", "button", (120, 160, 120, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (120, 160, 120, 32))

    def test_file_action_alias_model_rect_highlights_button(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Upload a file.",
                    "target": {"x": 120, "y": 160, "width": 120, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Browse", "button", (120, 160, 120, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (120, 160, 120, 32))

    def test_file_attachment_target_id_accepts_paperclip_icons(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Attach file.", "\U0001f4ce"),
            ("Add attachment.", "\U0001f587"),
            ("Upload file.", "\U0001f4ce"),
            ("Click the paperclip.", "Attach"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [ControlCandidate("c001", label, "button", (120, 160, 32, 32))],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, (120, 160, 32, 32))

    def test_file_attachment_icon_text_match_overrides_upload_geometry(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Attach file.",
                    "target": {"x": 300, "y": 160, "width": 100, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "\U0001f4ce", "button", (120, 160, 32, 32)),
                ControlCandidate("c002", "Upload", "button", (300, 160, 100, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (120, 160, 32, 32))

    def test_file_attachment_aliases_do_not_cross_clipboard_actions(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Paste into the note.", "\U0001f4ce"),
            ("Attach file.", "\U0001f4cb"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [ControlCandidate("c001", label, "button", (120, 160, 32, 32))],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, "target_id semantic mismatch")

    def test_file_attachment_rejects_taskbar_file_explorer_state_label(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            "File Explorer pinned",
            "File Explorer",
        )
        for label in cases:
            with self.subTest(label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": "Attach file.",
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            label,
                            "button",
                            (120, 160, 180, 32),
                            window_title="Taskbar",
                        ),
                    ],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, "target_id semantic mismatch")

    def test_file_picker_model_rect_rejects_taskbar_file_explorer_state_label(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open the file picker.",
                    "target": {"x": 120, "y": 160, "width": 180, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate(
                    "c001",
                    "File Explorer pinned",
                    "button",
                    (120, 160, 180, 32),
                    window_title="Taskbar",
                ),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.rejected_reason, "candidate snapshot no match")

    def test_file_attachment_ignores_taskbar_file_explorer_decoy(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Attach file.",
                    "target_id": "c001",
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Choose file", "button", (120, 160, 140, 32)),
                ControlCandidate(
                    "c002",
                    "File Explorer pinned",
                    "button",
                    (300, 160, 180, 32),
                    window_title="Taskbar",
                ),
            ],
        )

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (120, 160, 140, 32))

    def test_named_taskbar_app_label_still_matches_app_instruction(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click File Explorer.",
                    "target_id": "c001",
                }
            ),
            self._capture(),
            [
                ControlCandidate(
                    "c001",
                    "File Explorer pinned",
                    "button",
                    (120, 160, 180, 32),
                    window_title="Taskbar",
                ),
            ],
        )

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)

    def test_generic_view_rejects_tradingview_taskbar_app(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Open view.", "TradingView pinned", "target_id semantic mismatch"),
            ("Click view.", "TradingView pinned", "target_id semantic mismatch"),
            ("Open view.", "Task View", "target_id semantic mismatch"),
            ("Click view.", "Task View", "target_id semantic mismatch"),
            ("Open task.", "Task View", "target_id semantic mismatch"),
            ("Open Task View.", "Task View", ""),
        )
        for instruction, label, reason in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            label,
                            "button",
                            (120, 160, 180, 32),
                            automation_id="TaskViewButton" if label == "Task View" else "",
                            window_title="Taskbar",
                        ),
                    ],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, reason)

    def test_generic_view_does_not_recover_to_task_view_over_tradingview(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open view.",
                    "target_id": "c001",
                }
            ),
            self._capture(),
            [
                ControlCandidate(
                    "c001",
                    "TradingView pinned",
                    "button",
                    (120, 160, 180, 32),
                    window_title="Taskbar",
                ),
                ControlCandidate(
                    "c002",
                    "Task View",
                    "button",
                    (340, 160, 120, 32),
                    automation_id="TaskViewButton",
                    window_title="Taskbar",
                ),
            ],
        )

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.target_id, "c001")
        self.assertEqual(target.rejected_reason, "target_id semantic mismatch")

    def test_generic_task_or_view_model_rect_rejects_task_view_snap(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        for instruction in ("Open view.", "Open task.", "Click view button.", "Click task button."):
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target": {"x": 120, "y": 160, "width": 180, "height": 32},
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            "Task View",
                            "button",
                            (120, 160, 180, 32),
                            automation_id="TaskViewButton",
                            window_title="Taskbar",
                        ),
                    ],
                )

                self.assertEqual(target.source, "candidate_snap")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, "candidate semantic mismatch")

    def test_generic_task_text_match_ignores_task_view(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="",
            instruction="Open task.",
            candidates=[
                ControlCandidate(
                    "c001",
                    "Task View",
                    "button",
                    (120, 160, 180, 32),
                    automation_id="TaskViewButton",
                    window_title="Taskbar",
                ),
            ],
        )

        self.assertIsNone(result)

    def test_compound_taskbar_app_names_still_match(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Open TradingView.", "TradingView pinned"),
            ("Open trading view.", "TradingView pinned"),
            ("Open phone link.", "Phone Link pinned"),
            ("Open phone.", "Phone Link pinned"),
        )
        for instruction, label in cases:
            with self.subTest(instruction=instruction, label=label):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            label,
                            "button",
                            (120, 160, 180, 32),
                            window_title="Taskbar",
                        ),
                    ],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)

    def test_splitbutton_model_rect_highlights_dropdown_segment(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open the Export dropdown.",
                    "target": {"x": 100, "y": 100, "width": 180, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Export", "splitbutton", (100, 100, 180, 32)),
                ControlCandidate("c002", "Export", "button", (100, 100, 140, 32)),
                ControlCandidate("c003", "Export menu", "menuitem", (240, 100, 40, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c003")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (240, 100, 40, 32))

    def test_generic_row_model_rect_with_actions_downgrades_no_overlay(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click this button.",
                    "target": {"x": 10, "y": 10, "width": 600, "height": 80},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Account row", "listitem", (10, 10, 600, 80)),
                ControlCandidate("c002", "Edit", "button", (450, 20, 60, 30)),
                ControlCandidate("c003", "Delete", "button", (520, 20, 70, 30)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.rejected_reason, "candidate snapshot no match")

    def test_generic_field_model_rect_with_clear_action_highlights_field(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click this field.",
                    "target": {"x": 10, "y": 10, "width": 600, "height": 40},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Search", "edit", (10, 10, 600, 40)),
                ControlCandidate("c002", "Clear", "button", (570, 14, 28, 28)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (10, 10, 600, 40))

    def test_generic_checkbox_model_rect_does_not_highlight_button(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click this checkbox.",
                    "target": {"x": 10, "y": 10, "width": 32, "height": 32},
                }
            ),
            self._capture(),
            [ControlCandidate("c001", "", "button", (10, 10, 32, 32))],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.rejected_reason, "candidate snapshot no match")

    def test_generic_checkbox_row_model_rect_highlights_single_checkbox(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click this checkbox.",
                    "target": {"x": 10, "y": 10, "width": 600, "height": 80},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Task row", "listitem", (10, 10, 600, 80)),
                ControlCandidate("c002", "Done", "checkbox", (24, 34, 20, 20)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.target_id, "c002")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (24, 34, 20, 20))

    def test_generic_toggle_model_rect_highlights_checkbox(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click this toggle.",
                    "target": {"x": 10, "y": 10, "width": 200, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Dark mode", "checkbox", (10, 10, 200, 32)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (10, 10, 200, 32))

    def test_generic_switch_model_rect_highlights_checkbox(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click this switch.",
                    "target": {"x": 10, "y": 10, "width": 200, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Dark mode", "checkbox", (10, 10, 200, 32)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (10, 10, 200, 32))

    def test_toggle_sidebar_model_rect_highlights_button(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Toggle sidebar.",
                    "target": {"x": 10, "y": 10, "width": 150, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Toggle sidebar", "button", (10, 10, 150, 32)),
                ControlCandidate("c002", "Dark mode", "checkbox", (10, 60, 140, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (10, 10, 150, 32))

    def test_generic_option_model_rect_highlights_radio(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Select this option.",
                    "target": {"x": 10, "y": 10, "width": 140, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Weekly", "radiobutton", (10, 10, 140, 32)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (10, 10, 140, 32))

    def test_generic_option_broad_group_rejects_multiple_radios(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Select this option.",
                    "target": {"x": 10, "y": 10, "width": 140, "height": 96},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Daily", "radiobutton", (10, 10, 140, 32)),
                ControlCandidate("c002", "Weekly", "radiobutton", (10, 42, 140, 32)),
                ControlCandidate("c003", "Monthly", "radiobutton", (10, 74, 140, 32)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.rejected_reason, "candidate snapshot no match")

    def test_generic_slider_model_rect_highlights_slider(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Adjust this slider.",
                    "target": {"x": 10, "y": 10, "width": 220, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Volume", "slider", (10, 10, 220, 32)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (10, 10, 220, 32))

    def test_generic_spinner_model_rect_highlights_spinner(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Adjust this spinner.",
                    "target": {"x": 10, "y": 10, "width": 160, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "History max tokens", "spinner", (10, 10, 160, 32)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (10, 10, 160, 32))

    def test_spinner_stepper_model_rect_highlights_adjacent_arrow_button(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        candidates = [
            ControlCandidate("spin", "Quantity", "spinner", (100, 100, 120, 32)),
            ControlCandidate("up", "Increase", "button", (224, 100, 24, 16)),
            ControlCandidate("down", "Decrease", "button", (224, 116, 24, 16)),
        ]
        cases = (
            ("Increase the Quantity spinner.", "up", (224, 100, 24, 16)),
            ("Decrease the Quantity spinner.", "down", (224, 116, 24, 16)),
        )
        for instruction, target_id, rect in cases:
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": target_id,
                            "target": {"x": rect[0], "y": rect[1], "width": rect[2], "height": rect[3]},
                        }
                    ),
                    self._capture(),
                    candidates,
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, target_id)
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, rect)

    def test_generic_hyperlink_model_rect_highlights_hyperlink(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click this hyperlink.",
                    "target": {"x": 10, "y": 10, "width": 140, "height": 28},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Documentation", "hyperlink", (10, 10, 140, 28)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (10, 10, 140, 28))

    def test_generic_list_item_model_rect_highlights_listitem(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click this list item.",
                    "target": {"x": 10, "y": 10, "width": 160, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Settings", "listitem", (10, 10, 160, 32)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (10, 10, 160, 32))

    def test_generic_tree_item_model_rect_highlights_treeitem(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click this tree item.",
                    "target": {"x": 10, "y": 10, "width": 160, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Settings", "treeitem", (10, 10, 160, 32)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (10, 10, 160, 32))

    def test_compact_control_type_model_rect_highlights_exact_type(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("listitem", "listitem", (10, 10, 160, 32)),
            ("treeitem", "treeitem", (10, 10, 160, 32)),
            ("menuitem", "menuitem", (10, 10, 160, 28)),
            ("tabitem", "tabitem", (10, 10, 140, 32)),
            ("headeritem", "headeritem", (10, 10, 140, 28)),
            ("splitbutton", "splitbutton", (10, 10, 160, 32)),
        )
        for word, control_type, rect in cases:
            with self.subTest(word=word):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": f"Click this {word}.",
                            "target": {
                                "x": rect[0],
                                "y": rect[1],
                                "width": rect[2],
                                "height": rect[3],
                            },
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate("c001", "Settings", control_type, rect),
                    ],
                )

                self.assertEqual(target.source, "candidate_snap")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, rect)

    def test_generic_split_button_model_rect_highlights_splitbutton(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click this split button.",
                    "target": {"x": 10, "y": 10, "width": 160, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Export", "splitbutton", (10, 10, 160, 32)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (10, 10, 160, 32))

    def test_generic_split_button_broad_group_rejects_multiple_splitbuttons(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click this split button.",
                    "target": {"x": 10, "y": 10, "width": 160, "height": 112},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Export", "splitbutton", (10, 10, 160, 32)),
                ControlCandidate("c002", "Share", "splitbutton", (10, 50, 160, 32)),
                ControlCandidate("c003", "Archive", "splitbutton", (10, 90, 160, 32)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.rejected_reason, "candidate snapshot no match")

    def test_browser_url_bar_model_rect_highlights_address_edit(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Focus the URL bar.",
                    "target": {"x": 10, "y": 10, "width": 240, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Address", "edit", (10, 10, 240, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (10, 10, 240, 32))

    def test_browser_url_bar_broad_group_prefers_address_edit(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Focus the URL bar.",
                    "target": {"x": 10, "y": 10, "width": 240, "height": 112},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Address", "edit", (10, 10, 240, 32)),
                ControlCandidate("c002", "Search", "edit", (10, 50, 240, 32)),
                ControlCandidate("c003", "Filter", "edit", (10, 90, 240, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (10, 10, 240, 32))

    def test_browser_address_bar_rejects_url_content_info_wording(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        address = "about:blank | Address and search bar"
        cases = (
            "Open about.",
            "Show info.",
            "Open information.",
            "Open site information.",
            "Open blank.",
        )
        for instruction in cases:
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            address,
                            "edit",
                            (120, 160, 260, 32),
                            window_title="about:blank - Google Chrome",
                        )
                    ],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, "target_id semantic mismatch")

    def test_fresh_snap_rejects_address_bar_url_content_wording(self) -> None:
        from help_session import resolve_help_target
        from rect_snap import snap_to_control

        address = _make_button(
            "about:blank | Address and search bar",
            120,
            160,
            260,
            32,
            control_type="Edit",
        )
        desktop = _FakeDesktop(
            [_make_window("about:blank - Google Chrome", 0, 0, 1000, 600, [address])]
        )

        for instruction in ("Open about.", "Open blank.", "Show info."):
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target": {"x": 120, "y": 160, "width": 260, "height": 32},
                        }
                    ),
                    self._capture(),
                    [],
                    snapper=lambda rect, text: snap_to_control(
                        rect,
                        text,
                        desktop_factory=lambda: desktop,
                        timeout_ms=2000,
                    ),
                )

                self.assertEqual(target.source, "snap")
                self.assertEqual(target.matched_text, "about:blank | Address and search bar")
                self.assertEqual(target.rejected_reason, "candidate semantic mismatch")

    def test_browser_about_blank_tab_rejects_site_info_wording(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            "Show info.",
            "Open info.",
            "Open details.",
            "Open about.",
            "Show site info.",
            "View site information.",
        )
        for instruction in cases:
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            "about:blank",
                            "tabitem",
                            (120, 160, 220, 32),
                            window_title="about:blank - Google Chrome",
                        ),
                    ],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, "target_id semantic mismatch")

    def test_browser_about_blank_tab_accepts_explicit_tab_wording(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            "Open about blank.",
            "Open about:blank tab.",
            "Click about:blank tab.",
        )
        for instruction in cases:
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            "about:blank",
                            "tabitem",
                            (120, 160, 220, 32),
                            window_title="about:blank - Google Chrome",
                        ),
                    ],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)

    def test_site_info_text_match_recovers_from_about_blank_tab_title(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Show site info.",
                    "target_id": "c001",
                }
            ),
            self._capture(),
            [
                ControlCandidate(
                    "c001",
                    "about:blank",
                    "tabitem",
                    (120, 160, 220, 32),
                    window_title="about:blank - Google Chrome",
                ),
                ControlCandidate(
                    "c002",
                    "View site information",
                    "button",
                    (400, 160, 160, 32),
                    window_title="about:blank - Google Chrome",
                ),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c002")
        self.assertFalse(target.rejected_reason)

    def test_browser_menu_wording_accepts_chrome_menu_button(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            "Open more menu.",
            "Open options menu.",
            "Open more options menu.",
            "Open Chrome menu.",
        )
        for instruction in cases:
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            "Chrome",
                            "button",
                            (120, 160, 60, 32),
                            window_title="about:blank - Google Chrome",
                        ),
                    ],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)

    def test_browser_window_page_menu_button_beats_chrome_menu_fallback(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open the more options menu.",
                    "target_id": "c001",
                    "target": {"x": 400, "y": 400, "width": 120, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate(
                    "c001",
                    "More options",
                    "button",
                    (400, 400, 120, 32),
                    window_title="Project - Google Chrome",
                ),
                ControlCandidate(
                    "c002",
                    "Chrome",
                    "button",
                    (930, 8, 40, 34),
                    automation_id="view_1007",
                    window_title="Project - Google Chrome",
                ),
            ],
        )

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)

    def test_generic_menu_button_rejects_browser_navigation_toolbar_button(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        back = ControlCandidate(
            "c001",
            "Back",
            "button",
            (120, 160, 34, 34),
            automation_id="view_1001",
            window_title="about:blank - Google Chrome",
        )
        chrome = ControlCandidate(
            "c002",
            "Chrome",
            "button",
            (300, 160, 40, 34),
            automation_id="view_1007",
            window_title="about:blank - Google Chrome",
        )
        cases = (
            (
                {
                    "kind": "step",
                    "instruction": "Click menu button.",
                    "target_id": "c001",
                },
                "target_id",
                "target_id semantic mismatch",
            ),
            (
                {
                    "kind": "step",
                    "instruction": "Click menu button.",
                    "target": {"x": 120, "y": 160, "width": 34, "height": 34},
                },
                "candidate_snap",
                "candidate semantic mismatch",
            ),
            (
                {
                    "kind": "step",
                    "instruction": "Click menu button.",
                    "target_id": "c002",
                    "target": {"x": 300, "y": 160, "width": 40, "height": 34},
                },
                "target_id",
                "",
            ),
        )
        for decision, source, reason in cases:
            with self.subTest(decision=decision):
                target = resolve_help_target(
                    self._decision(decision),
                    self._capture(),
                    [back, chrome],
                )

                self.assertEqual(target.source, source)
                self.assertEqual(target.rejected_reason, reason)

    def test_generic_browser_menu_wording_rejects_hidden_bookmarks_overflow(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            "Open more menu.",
            "Open options menu.",
            "Open more options menu.",
            "Open all bookmarks.",
            "Open hidden.",
        )
        for instruction in cases:
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            "Menu containing hidden bookmarks",
                            "button",
                            (120, 160, 220, 32),
                            window_title="about:blank - Google Chrome",
                        ),
                    ],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertEqual(target.rejected_reason, "target_id semantic mismatch")

    def test_bare_hidden_text_match_ignores_hidden_bookmarks_overflow(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        target = resolve_candidate_target(
            target_id="",
            instruction="Open hidden.",
            candidates=[
                ControlCandidate(
                    "c001",
                    "Menu containing hidden bookmarks",
                    "button",
                    (120, 160, 220, 32),
                    window_title="about:blank - Google Chrome",
                ),
            ],
            model_rect=None,
        )

        self.assertIsNone(target)

    def test_browser_menu_text_match_recovers_from_hidden_bookmarks_overflow(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            "Open more options menu.",
            "Open Chrome menu.",
        )
        for instruction in cases:
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c002",
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            "Chrome",
                            "button",
                            (120, 160, 60, 32),
                            window_title="about:blank - Google Chrome",
                        ),
                        ControlCandidate(
                            "c002",
                            "Menu containing hidden bookmarks",
                            "button",
                            (240, 160, 220, 32),
                            window_title="about:blank - Google Chrome",
                        ),
                    ],
                )

                self.assertEqual(target.source, "text_match")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)

    def test_bare_all_rejects_all_bookmarks_button(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open all.",
                    "target_id": "c001",
                }
            ),
            self._capture(),
            [
                ControlCandidate(
                    "c001",
                    "All Bookmarks",
                    "button",
                    (120, 160, 160, 32),
                    window_title="about:blank - Google Chrome",
                ),
            ],
        )

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.target_id, "c001")
        self.assertEqual(target.rejected_reason, "target_id semantic mismatch")

    def test_all_bookmarks_wording_still_matches_all_bookmarks_button(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = ("Open all bookmarks.", "Open bookmarks.")
        for instruction in cases:
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            "All Bookmarks",
                            "button",
                            (120, 160, 160, 32),
                            window_title="about:blank - Google Chrome",
                        ),
                    ],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)

    def test_hidden_bookmarks_overflow_accepts_hidden_bookmark_wording(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            "Open hidden bookmarks.",
            "Show hidden bookmarks.",
            "Open bookmarks menu.",
        )
        for instruction in cases:
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            "Menu containing hidden bookmarks",
                            "button",
                            (120, 160, 220, 32),
                            window_title="about:blank - Google Chrome",
                        ),
                    ],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)

    def test_browser_address_bar_live_label_accepts_explicit_bar_wording(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            "Click address bar.",
            "Focus the URL bar.",
            "Click the search bar.",
            "Click the omnibox.",
        )
        for instruction in cases:
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target_id": "c001",
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate(
                            "c001",
                            "about:blank | Address and search bar",
                            "edit",
                            (120, 160, 260, 32),
                            window_title="about:blank - Google Chrome",
                        )
                    ],
                )

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, (120, 160, 260, 32))

    def test_site_info_text_match_recovers_from_address_bar_url_content(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Open site information.",
                    "target_id": "c001",
                }
            ),
            self._capture(),
            [
                ControlCandidate(
                    "c001",
                    "about:blank | Address and search bar",
                    "edit",
                    (120, 160, 260, 32),
                    window_title="about:blank - Google Chrome",
                ),
                ControlCandidate(
                    "c002",
                    "View site information",
                    "button",
                    (420, 160, 160, 32),
                    window_title="about:blank - Google Chrome",
                ),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c002")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (420, 160, 160, 32))

    def test_button_control_suffix_model_rect_highlights_button(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click this button control.",
                    "target": {"x": 10, "y": 10, "width": 120, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Submit", "button", (10, 10, 120, 32)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (10, 10, 120, 32))

    def test_literal_edit_model_rect_highlights_edit(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click this edit control.",
                    "target": {"x": 10, "y": 10, "width": 240, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Search", "edit", (10, 10, 240, 32)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (10, 10, 240, 32))

    def test_contextual_container_model_rect_highlights_exact_control(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        cases = (
            ("Click this toolbar button.", "Save", "button", (10, 10, 100, 32)),
            ("Click Toolbar button.", "Toolbar", "button", (10, 10, 100, 32)),
            ("Click this toolbar icon.", "Settings", "button", (10, 10, 32, 32)),
            ("Click this form field.", "Name", "edit", (10, 10, 240, 32)),
            ("Click this dialog button.", "OK", "button", (10, 10, 80, 32)),
            ("Click this modal button.", "OK", "button", (10, 10, 80, 32)),
            ("Click this panel button.", "Save", "button", (10, 10, 100, 32)),
            ("Click Panel button.", "Panel", "button", (10, 10, 100, 32)),
            ("Click this table row.", "Order 123", "listitem", (10, 10, 240, 32)),
            ("Click this grid row.", "Order 123", "listitem", (10, 10, 240, 32)),
            ("Click this page link.", "Docs", "hyperlink", (10, 10, 120, 28)),
            ("Click this card checkbox.", "Done", "checkbox", (10, 10, 160, 32)),
            ("Click this section toggle.", "Dark mode", "checkbox", (10, 10, 160, 32)),
            ("Click this drawer item.", "Settings", "listitem", (10, 10, 160, 32)),
            ("Click this pane button.", "Apply", "button", (10, 10, 100, 32)),
            ("Click this popup menu item.", "Open", "menuitem", (10, 10, 120, 28)),
            ("Click this navigation tab.", "Settings", "tabitem", (10, 10, 140, 32)),
            ("Click this sidebar item.", "Settings", "listitem", (10, 10, 160, 32)),
            ("Click this nav item.", "Settings", "listitem", (10, 10, 160, 32)),
        )
        for instruction, label, control_type, rect in cases:
            with self.subTest(instruction=instruction):
                target = resolve_help_target(
                    self._decision(
                        {
                            "kind": "step",
                            "instruction": instruction,
                            "target": {
                                "x": rect[0],
                                "y": rect[1],
                                "width": rect[2],
                                "height": rect[3],
                            },
                        }
                    ),
                    self._capture(),
                    [
                        ControlCandidate("c001", label, control_type, rect),
                    ],
                )

                self.assertEqual(target.target_id, "c001")
                self.assertFalse(target.rejected_reason)
                self.assertEqual(target.rect, rect)

    def test_sidebar_item_broad_group_rejects_multiple_listitems(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click this sidebar item.",
                    "target": {"x": 10, "y": 10, "width": 160, "height": 112},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "General", "listitem", (10, 10, 160, 32)),
                ControlCandidate("c002", "Privacy", "listitem", (10, 50, 160, 32)),
                ControlCandidate("c003", "Billing", "listitem", (10, 90, 160, 32)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.rejected_reason, "candidate snapshot no match")

    def test_sidebar_item_rejects_browser_tabitem_collision(self) -> None:
        from control_inventory import (
            ControlCandidate,
            resolve_candidate_target,
            snap_candidate_target,
        )
        from help_session import resolve_help_target

        browser_tab = ControlCandidate(
            "c001",
            "Settings - MyApp - Google Chrome",
            "tabitem",
            (20, 10, 220, 32),
            window_title="MyApp - Google Chrome",
        )
        sidebar_item = ControlCandidate(
            "c002",
            "Settings",
            "listitem",
            (10, 120, 180, 32),
            window_title="MyApp - Google Chrome",
        )

        target_id = resolve_candidate_target(
            target_id="c001",
            instruction="Click the Settings sidebar item.",
            candidates=[browser_tab, sidebar_item],
        )
        text_target = resolve_candidate_target(
            target_id="",
            instruction="Click the Settings sidebar item.",
            candidates=[browser_tab, sidebar_item],
        )
        snap_target = snap_candidate_target(
            instruction="Click the Settings sidebar item.",
            candidates=[browser_tab],
            model_rect=browser_tab.rect,
        )
        help_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click the Settings sidebar item.",
                    "target": {
                        "x": browser_tab.rect[0],
                        "y": browser_tab.rect[1],
                        "width": browser_tab.rect[2],
                        "height": browser_tab.rect[3],
                    },
                }
            ),
            self._capture(),
            [browser_tab, sidebar_item],
        )

        self.assertEqual(target_id.source, "target_id")
        self.assertEqual(target_id.rejected_reason, "target_id semantic mismatch")
        self.assertEqual(text_target.source, "text_match")
        self.assertEqual(text_target.target_id, "c002")
        self.assertEqual(snap_target.source, "candidate_snap")
        self.assertEqual(snap_target.rejected_reason, "candidate semantic mismatch")
        self.assertEqual(help_target.target_id, "c002")
        self.assertFalse(help_target.rejected_reason)

    def test_named_page_route_rejects_browser_tabitem_collision(self) -> None:
        from control_inventory import (
            ControlCandidate,
            resolve_candidate_target,
            snap_candidate_target,
        )
        from help_session import resolve_help_target

        browser_tab = ControlCandidate(
            "tab",
            "Customers - MyApp - Google Chrome",
            "tabitem",
            (20, 10, 220, 32),
            window_title="MyApp - Google Chrome",
        )
        page_item = ControlCandidate(
            "customers",
            "Customers",
            "listitem",
            (20, 120, 180, 32),
            window_title="MyApp - Google Chrome",
        )
        instruction = "Open Customers page."

        target_id = resolve_candidate_target(
            target_id="tab",
            instruction=instruction,
            candidates=[browser_tab, page_item],
            model_rect=browser_tab.rect,
        )
        text_target = resolve_candidate_target(
            target_id="",
            instruction=instruction,
            candidates=[browser_tab, page_item],
            model_rect=browser_tab.rect,
        )
        snap_target = snap_candidate_target(
            instruction=instruction,
            candidates=[browser_tab, page_item],
            model_rect=browser_tab.rect,
        )
        help_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": instruction,
                    "target_id": "tab",
                    "target": {"x": 20, "y": 10, "width": 220, "height": 32},
                }
            ),
            self._capture(),
            [browser_tab, page_item],
        )

        self.assertEqual(target_id.source, "target_id")
        self.assertEqual(target_id.rejected_reason, "target_id semantic mismatch")
        self.assertEqual(text_target.source, "text_match")
        self.assertEqual(text_target.target_id, "customers")
        self.assertFalse(text_target.rejected_reason)
        self.assertEqual(snap_target.source, "candidate_snap")
        self.assertEqual(snap_target.rejected_reason, "candidate semantic mismatch")
        self.assertEqual(help_target.source, "text_match")
        self.assertEqual(help_target.target_id, "customers")
        self.assertFalse(help_target.rejected_reason)

    def test_table_row_broad_group_rejects_multiple_listitems(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click this table row.",
                    "target": {"x": 10, "y": 10, "width": 240, "height": 112},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Order 1", "listitem", (10, 10, 240, 32)),
                ControlCandidate("c002", "Order 2", "listitem", (10, 50, 240, 32)),
                ControlCandidate("c003", "Order 3", "listitem", (10, 90, 240, 32)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.rejected_reason, "candidate snapshot no match")

    def test_generic_slider_broad_group_rejects_multiple_sliders(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Adjust this slider.",
                    "target": {"x": 10, "y": 10, "width": 220, "height": 112},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Volume", "slider", (10, 10, 220, 32)),
                ControlCandidate("c002", "Brightness", "slider", (10, 50, 220, 32)),
                ControlCandidate("c003", "Contrast", "slider", (10, 90, 220, 32)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.rejected_reason, "candidate snapshot no match")

    def test_generic_spinner_broad_group_rejects_multiple_spinners(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Adjust this spinner.",
                    "target": {"x": 10, "y": 10, "width": 160, "height": 112},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Temperature", "spinner", (10, 10, 160, 32)),
                ControlCandidate("c002", "Retries", "spinner", (10, 50, 160, 32)),
                ControlCandidate("c003", "Delay", "spinner", (10, 90, 160, 32)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.rejected_reason, "candidate snapshot no match")

    def test_generic_hyperlink_broad_group_rejects_multiple_hyperlinks(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click this hyperlink.",
                    "target": {"x": 10, "y": 10, "width": 120, "height": 100},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Docs", "hyperlink", (10, 10, 120, 28)),
                ControlCandidate("c002", "Support", "hyperlink", (10, 46, 120, 28)),
                ControlCandidate("c003", "Pricing", "hyperlink", (10, 82, 120, 28)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.rejected_reason, "candidate snapshot no match")

    def test_generic_list_item_broad_group_rejects_multiple_listitems(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click this list item.",
                    "target": {"x": 10, "y": 10, "width": 160, "height": 112},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "General", "listitem", (10, 10, 160, 32)),
                ControlCandidate("c002", "Privacy", "listitem", (10, 50, 160, 32)),
                ControlCandidate("c003", "Billing", "listitem", (10, 90, 160, 32)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.rejected_reason, "candidate snapshot no match")

    def test_generic_tree_item_broad_group_rejects_multiple_treeitems(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click this tree item.",
                    "target": {"x": 10, "y": 10, "width": 160, "height": 112},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "src", "treeitem", (10, 10, 160, 32)),
                ControlCandidate("c002", "tests", "treeitem", (10, 50, 160, 32)),
                ControlCandidate("c003", "docs", "treeitem", (10, 90, 160, 32)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.rejected_reason, "candidate snapshot no match")

    def test_generic_column_header_model_rect_highlights_header(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click this column header.",
                    "target": {"x": 100, "y": 50, "width": 120, "height": 28},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Status", "headeritem", (100, 50, 120, 28)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (100, 50, 120, 28))

    def test_generic_column_header_broad_row_rejects_multiple_headers(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click this column header.",
                    "target": {"x": 20, "y": 50, "width": 360, "height": 28},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Name", "headeritem", (20, 50, 120, 28)),
                ControlCandidate("c002", "Status", "headeritem", (140, 50, 120, 28)),
                ControlCandidate("c003", "Owner", "headeritem", (260, 50, 120, 28)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.rejected_reason, "candidate snapshot no match")

    def test_contextual_checkbox_row_highlights_single_checkbox(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click the checkbox in Task row.",
                    "target": {"x": 10, "y": 10, "width": 600, "height": 80},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Task row", "listitem", (10, 10, 600, 80)),
                ControlCandidate("c002", "Done", "checkbox", (24, 34, 20, 20)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c002")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (24, 34, 20, 20))

    def test_contextual_checkbox_row_rejects_multiple_checkboxes(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click the checkbox in Task row.",
                    "target": {"x": 10, "y": 10, "width": 600, "height": 80},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Task row", "listitem", (10, 10, 600, 80)),
                ControlCandidate("c002", "Done", "checkbox", (24, 24, 20, 20)),
                ControlCandidate("c003", "Archived", "checkbox", (24, 52, 20, 20)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.rejected_reason, "candidate snapshot no match")

    def test_generic_model_rect_rejects_background_snap_when_foreground_is_plausible(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click this button.",
                    "target": {"x": 120, "y": 136, "width": 80, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Save", "button", (120, 100, 80, 32), window_rank=0),
                ControlCandidate("c002", "Save", "button", (120, 145, 80, 32), window_rank=2),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.target_id, "c002")
        self.assertEqual(target.rejected_reason, "ambiguous candidate snap")

    def test_model_rect_on_mismatched_candidate_rejects_instead_of_raw_overlay(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target
        from rect_snap import SnapResult

        model_rect = (120, 160, 80, 32)
        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Save.",
                    "target": {"x": 120, "y": 160, "width": 80, "height": 32},
                }
            ),
            self._capture(),
            [ControlCandidate("c001", "Cancel", "button", model_rect)],
            snapper=lambda rect, _instruction: SnapResult(
                rect=rect,
                confidence=0.41,
                source="model",
                matched_text="Cancel",
            ),
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.matched_text, "Cancel")
        self.assertEqual(target.rejected_reason, "candidate semantic mismatch")

    def test_candidate_snapshot_miss_does_not_call_fresh_snapper(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        calls: list[bool] = []

        def snapper(_rect, _instruction):
            calls.append(True)
            raise AssertionError("fresh snapper should not run after candidate snapshot no-match")

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Save.",
                    "target": {"x": 420, "y": 420, "width": 80, "height": 32},
                }
            ),
            self._capture(),
            [ControlCandidate("c001", "Cancel", "button", (120, 160, 80, 32))],
            snapper=snapper,
        )

        self.assertFalse(calls)
        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.rejected_reason, "candidate snapshot no match")

    def test_rejected_fresh_snap_does_not_fall_back_to_raw_model_rect(self) -> None:
        from help_session import resolve_help_target
        from rect_snap import SnapResult

        model_rect = (120, 160, 80, 32)
        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Save.",
                    "target": {"x": 120, "y": 160, "width": 80, "height": 32},
                }
            ),
            self._capture(),
            [],
            snapper=lambda _rect, _instruction: SnapResult(
                rect=model_rect,
                confidence=0.41,
                source="uia",
                matched_text="Cancel saveButton",
                rejected_reason="candidate semantic mismatch",
            ),
        )

        self.assertEqual(target.source, "snap")
        self.assertEqual(target.rect, model_rect)
        self.assertEqual(target.rejected_reason, "candidate semantic mismatch")

    def test_fresh_snap_control_type_mismatch_does_not_fall_back_to_raw_model_rect(self) -> None:
        from help_session import resolve_help_target
        from rect_snap import snap_to_control

        button = _make_button("", 120, 160, 32, 32)
        window = _make_window("Settings", 0, 0, 800, 600, [button])
        desktop = _FakeDesktop([window])

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click this checkbox.",
                    "target": {"x": 120, "y": 160, "width": 32, "height": 32},
                }
            ),
            self._capture(),
            [],
            snapper=lambda rect, instruction: snap_to_control(
                rect,
                instruction,
                desktop_factory=lambda: desktop,
                timeout_ms=2000,
            ),
        )

        self.assertEqual(target.source, "snap")
        self.assertEqual(target.rect, (120, 160, 32, 32))
        self.assertEqual(target.rejected_reason, "control type mismatch")

    def test_oversized_model_rect_is_rejected(self) -> None:
        from help_session import resolve_help_target
        from rect_snap import SnapResult

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click the button.",
                    "target": {"x": 100, "y": 100, "width": 600, "height": 300},
                }
            ),
            self._capture(),
            [],
            snapper=lambda rect, _instruction: SnapResult(
                rect=rect,
                confidence=0.0,
                source="model",
            ),
        )

        self.assertEqual(target.source, "model")
        self.assertEqual(target.rejected_reason, "oversized target")

    def test_partially_offscreen_candidate_is_clipped_before_display(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import clip_resolution_to_capture, resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Edge.",
                    "target_id": "c001",
                    "target": {"x": 0, "y": 120, "width": 40, "height": 30},
                }
            ),
            self._capture(),
            [ControlCandidate("c001", "Edge", "button", (-20, 120, 60, 30))],
        )

        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (0, 120, 40, 30))

        raw_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Edge.",
                    "target_id": "c001",
                    "target": {"x": 0, "y": 120, "width": 40, "height": 30},
                }
            ),
            self._capture(),
            [ControlCandidate("c001", "Edge", "button", (-20, 120, 60, 30))],
            clip_to_capture=False,
        )
        clipped = clip_resolution_to_capture(raw_target, self._capture())
        self.assertEqual(raw_target.rect, (-20, 120, 60, 30))
        self.assertEqual(clipped.rect, (0, 120, 40, 30))


class LooksOversizedTests(unittest.TestCase):
    def _make_decision(self, w: int, h: int):
        from agent import LiveHelpDecision

        return LiveHelpDecision(
            kind="step",
            instruction="placeholder",
            target_norm_x=100,
            target_norm_y=200,
            target_norm_width=w,
            target_norm_height=h,
        )

    def test_normal_button_not_oversized(self) -> None:
        from help_session import looks_oversized

        self.assertFalse(looks_oversized(self._make_decision(80, 30)))

    def test_wide_input_not_oversized(self) -> None:
        from help_session import looks_oversized

        self.assertFalse(looks_oversized(self._make_decision(300, 40)))

    def test_panel_sized_box_is_oversized_by_area(self) -> None:
        from help_session import looks_oversized

        self.assertTrue(looks_oversized(self._make_decision(400, 300)))

    def test_very_wide_strip_is_oversized_by_edge(self) -> None:
        from help_session import looks_oversized

        self.assertTrue(looks_oversized(self._make_decision(450, 30)))

    def test_very_tall_column_is_oversized_by_edge(self) -> None:
        from help_session import looks_oversized

        self.assertTrue(looks_oversized(self._make_decision(40, 450)))


if __name__ == "__main__":
    unittest.main()
