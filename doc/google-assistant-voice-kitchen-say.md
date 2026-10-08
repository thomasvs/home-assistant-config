# Kitchen Say: Voice Commands Across Google Assistant & Assist Surfaces

This document describes how to use the `kitchen say ...` voice command across all Google Assistant and Home Assistant Assist surfaces (smart speakers, Pixel Watch, Android phones).

---

## 1. Smart Speakers (Google Home, Nest Mini, Nest Audio, Pixel Tablet in Hub Mode)

### A. Freeform Announcements via Native Room Broadcast
Google Assistant on all smart speakers supports broadcasting directly to specific rooms:
* **"Hey Google, broadcast to the kitchen <message>"**
* **"Hey Google, tell the kitchen <message>"**
* **"Hey Google, announce to the kitchen <message>"**

Google Assistant will speak your message directly on the Google Home / Nest speaker located in the **Kitchen** (`media_player.kitchen_home`).

### B. Preset Announcement Scripts (Voice & Routines)
Home Assistant exposes dedicated kitchen announcement scripts to Google Assistant (under `room: Kitchen`):
* **"Hey Google, activate Dinner Ready"** (or *"turn on Dinner Ready"*)
  * Speaks: *"Dinner is ready!"*
* **"Hey Google, activate Time to Eat"**
  * Speaks: *"Time to eat!"*
* **"Hey Google, activate Come to Kitchen"**
  * Speaks: *"Come to the kitchen please!"*
* **"Hey Google, activate Kitchen Say"** (or *"turn on Kitchen Announcement"*)
  * Speaks the current message stored in `input_text.kitchen_say`

### C. Google Home App Custom Routines (Optional Voice Shortcuts)
You can create Household or Personal Routines in the Google Home app to map custom phrases:
1. Open **Google Home app** > **Automations** > **Add (+)**.
2. **Starter**: When I say to Google Assistant:
   * *"kitchen say dinner is ready"*
3. **Action**: Adjust Home Devices > Activate Scene > **Dinner Ready** (or Assistant Action: *"Broadcast to the kitchen dinner is ready"*).

---

## 2. Pixel Watch (Wear OS)

### A. Home Assistant Assist Complication / Tile
1. Tap the **Assist** complication on your watch face or swipe to the **Home Assistant Assist Tile**.
2. Speak or type:
   * *"kitchen say <message>"*
   * *"say <message> in the kitchen"*
   * *"broadcast to kitchen <message>"*
   * *"announce to kitchen <message>"*
3. Assist matches the `conversation_kitchen_say` intent and executes `script.kitchen_say` with your message, speaking on `media_player.pixel_tablet` and `media_player.kitchen_home`.

### B. Google Assistant on Pixel Watch
* Speak to Google Assistant on your watch:
  * *"Hey Google, ask Home Assistant to kitchen say <message>"*
  * *"Hey Google, broadcast to the kitchen <message>"*
  * *"Hey Google, activate Dinner Ready"*

---

## 3. Android Phone (Pixel 10 Pro Fold / Pixel Devices)

### A. Google Assistant App Action
* *"Hey Google, ask Home Assistant to kitchen say <message>"*
* *"Hey Google, broadcast to the kitchen <message>"*

### B. Home Assistant Quick Settings / Assist Widget
* Tap the **Assist** widget or Quick Settings tile.
* Speak: *"kitchen say <message>"*.

---

## 4. Kitchen Lovelace Dashboard

For manual or visual triggers, visit the **Kitchen** dashboard tab:
* **Message to Speak**: Type any custom message into `input_text.kitchen_say`.
* **Speak on Kitchen Speaker**: Tap the bullhorn button to execute `script.kitchen_say_from_input`.
* **Quick Buttons**: 1-tap buttons for *Dinner Ready*, *Time to Eat*, and *Come to Kitchen*.
