# HGSmart Pet Feeder Integration for Home Assistant

A custom Home Assistant integration for Honey Guardian/Guaridan/Guaridian S25T Pet Feeder, providing a replacement for the (horrid) HGSmart app.

This integration was developed through reverse engineering of the HGSmart Android application and provides access to feeding schedules, manual feeding, food level monitoring, and device status.

## Features

- Manual feeding button with configurable portions
- Access to the programmable feeding schedules of the device
- Real-time food level monitoring
- Desiccant expiration tracking and reset
- Service for custom integrations / schedules (but you'll need to trust the HGSmart APIs and the pet feeder Wi-Fi connectivity)
- Eating sensors for supported devices
- Event logs

## Example cards

All example cards use the S25D model, remember to replace any `s25d` occurrence with your device model (ie: `s30d`)

### Simple

<img width="460" alt="basic" src="https://github.com/user-attachments/assets/906b3d01-d9f9-42b4-b8e0-a98aa6fab370" />

**Required custom cards**: layout-cards, lovelace-mushroom, slider-button-card

<details>

<summary>Click here to see the card YAML</summary>

```yaml
type: custom:layout-card
layout_type: custom:vertical
cards:
  - type: custom:mushroom-title-card
    title: Cat Kibble
  - type: custom:layout-card
    layout_type: custom:grid-layout
    layout:
      grid-template-columns: 70% 30%
      padding: 0px
      margin: 0px
    cards:
      - type: custom:mushroom-entity-card
        entity: button.s25d_feed
        icon_color: accent
        tap_action:
          action: more-info
        hold_action:
          action: more-info
        double_tap_action:
          action: more-info
        name: Feed the beasts
        fill_container: false
      - type: custom:mushroom-number-card
        entity: number.s25d_manual_feed_portions
        name: Porzioni
        fill_container: false
        secondary_info: none
        layout: horizontal
        icon_type: none
        display_mode: buttons
        primary_info: none
  - type: custom:layout-card
    layout_type: custom:grid-layout
    layout:
      grid-template-columns: 35% 15% 50%
      padding: 0px
      margin: 0px;
    cards:
      - type: custom:mushroom-entity-card
        entity: sensor.s25d_desiccant_expiry
        name: Desiccant
        fill_container: true
      - show_name: false
        show_icon: true
        type: button
        entity: button.s25d_reset_desiccant
        icon: mdi:refresh
        theme: minimalist-desktop
      - type: custom:slider-button-card
        entity: number.s25d_set_food_remaining
        slider:
          direction: left-right
          background: solid
          use_percentage_bg_opacity: false
          show_track: true
          toggle_on_click: false
          force_square: false
        show_name: true
        show_state: true
        compact: true
        icon:
          show: true
          tap_action:
            action: more-info
          icon: mdi:cookie
        action_button:
          mode: toggle
          icon: mdi:power
          show: false
          show_spinner: true
          tap_action:
            action: toggle
        show_attribute: true
        name: Kibble
```

</details>

### Fully featured

<img  width="460" alt="full" src="https://github.com/user-attachments/assets/5e0df1ea-dfc9-4335-b68a-97180d19dc52" />

(thanks @sovanyio)

**Required custom cards**: layout-cards, lovelace-mushroom, slider-button-card, flex-table-card, bubble-card

<details>

<summary>Click here to see the card YAML</summary>

```yaml
type: custom:layout-card
layout_type: custom:vertical
cards:
  - type: custom:layout-card
    layout_type: custom:vertical
    layout:
      padding: 0px
      margin: 0px
    cards:
      - type: custom:mushroom-title-card
        title: Cat Feeder
      - type: custom:layout-card
        layout_type: custom:grid-layout
        layout:
          grid-template-columns: 70% 30%
          padding: 0px
          margin: 0px
        cards:
          - type: custom:mushroom-entity-card
            entity: button.s25d_feed
            icon_color: accent
            tap_action:
              action: more-info
            hold_action:
              action: more-info
            double_tap_action:
              action: more-info
            name: Feed the kitties
            fill_container: false
            primary_info: name
            secondary_info: last-changed
          - type: custom:mushroom-number-card
            entity: number.s25d_manual_feed_portions
            name: Portions
            fill_container: false
            secondary_info: none
            layout: horizontal
            icon_type: none
            display_mode: buttons
            primary_info: none
      - type: custom:layout-card
        layout_type: custom:grid-layout
        layout:
          grid-template-columns: 35% 15% 50%
          padding: 0px
          margin: 0px;
        cards:
          - type: custom:mushroom-entity-card
            entity: sensor.s25d_desiccant_expiry
            name: Desiccant
            fill_container: true
          - show_name: false
            show_icon: true
            type: button
            entity: button.s25d_reset_desiccant
            icon: mdi:refresh
            theme: minimalist-desktop
          - type: custom:slider-button-card
            entity: number.s25d_set_food_remaining
            slider:
              direction: left-right
              background: solid
              use_percentage_bg_opacity: false
              show_track: true
              toggle_on_click: false
              force_square: false
            show_name: true
            show_state: true
            compact: true
            icon:
              show: true
              tap_action:
                action: more-info
              icon: mdi:cookie
            action_button:
              mode: toggle
              icon: mdi:power
              show: false
              show_spinner: true
              tap_action:
                action: toggle
            show_attribute: true
            name: Kibble
      - type: custom:layout-card
        layout_type: custom:grid-layout
        layout:
          grid-template-columns: 50% 50%
          padding: 0px
          margin: 0px
        cards:
          - type: custom:mushroom-entity-card
            entity: switch.s25d_schedule_1_enabled
            icon: mdi:calendar-clock
            icon_color: green
            tap_action:
              action: navigate
              navigation_path: "#feeding-schedule"
            hold_action:
              action: navigate
              navigation_path: "#feeding-schedule"
            double_tap_action:
              action: navigate
              navigation_path: "#feeding-schedule"
            name: Edit Schedule
            fill_container: false
            primary_info: name
            secondary_info: none
          - type: custom:mushroom-entity-card
            entity: switch.s25d_schedule_1_enabled
            icon: mdi:cogs
            icon_color: purple
            tap_action:
              action: navigate
              navigation_path: "#feeding-settings"
            hold_action:
              action: navigate
              navigation_path: "#feeding-settings"
            double_tap_action:
              action: navigate
              navigation_path: "#feeding-settings"
            name: Device Settings
            fill_container: false
            primary_info: name
            secondary_info: none
  - type: custom:mushroom-title-card
    title: Feed log
  - type: custom:flex-table-card
    entities:
      - entity: sensor.s25d_today_s_events
    css:
      tr:has(td [data-bowl='right']): "background-color: light-dark(#e3f2fd, #0d47a1) !important;"
      tr:has(td [data-bowl='left']): "background-color: light-dark(#fff3e0, #e65100) !important;"
      tr:has(td [data-bowl='none']): "font-weight: bold;"
      th:nth-child(3): "display: none;"
      tr td:nth-child(3): "display: none;"
      td:nth-child(2): "padding: .5rem"
    columns:
      - name: Time
        data: events.createTime
        modify: >-
          (new Date(x.replace(' ',
          'T'))).toLocaleTimeString('it-IT').toLowerCase()
        align: right
      - name: Activity
        data: events.eventDesc
      - name: Code
        data: events.event
        modify: >
          (x == '1_10' && `<span data-bowl="right">${x}</span>`) || (x == '1_9'
          && `<span data-bowl="left">${x}</span>`) || (x == '1_1' && `<span
          data-bowl="none">${x}</span>`) || x
    strict: true
  - type: vertical-stack
    cards:
      - type: custom:bubble-card
        card_type: pop-up
        hash: "#feeding-schedule"
        button_type: name
        sub_button:
          main: []
          bottom: []
        show_header: false
      - type: custom:mushroom-title-card
        title: Feeding Schedule
      - type: custom:layout-card
        layout_type: custom:grid-layout
        layout:
          grid-template-columns: auto auto 1fr
        cards:
          - type: custom:mushroom-template-card
            entity: switch.s25d_schedule_1_enabled
            primary_info: none
            secondary_info: none
            icon: |
              {% if is_state(entity, 'on') %}
                mdi:paw
              {% else %}
                mdi:paw-off-outline
              {% endif %}
            color: |
              {% if is_state(entity, 'on') %}
                primary
              {% else %}
                disabled
              {% endif %}
          - type: custom:mushroom-template-card
            entity: number.s25d_schedule_1_portions
            icon: |
              {% if is_state('switch.25d_schedule_1_enabled', 'on') %}
                mdi:food
              {% else %}
                mdi:food-off-outline
              {% endif %}
            color: |
              {% if is_state('switch.s25d_schedule_1_enabled', 'on') %}
                primary
              {% else %}
                disabled
              {% endif %}
            badge_text: "{{ states(entity) }}"
            badge_color: |
              {% if is_state('switch.s25d_schedule_1_enabled', 'on') %}
                accent
              {% else %}
                disabled
              {% endif %}
          - type: custom:mushroom-template-card
            entity: time.s25d_schedule_1_time
            icon: |
              {% if is_state('switch.s25d_schedule_1_enabled', 'on') %}
                mdi:clock
              {% else %}
                mdi:clock-outline
              {% endif %}
            color: |
              {% if is_state('switch.s25d_schedule_1_enabled', 'on') %}
                primary
              {% else %}
                disabled
              {% endif %}
            primary: "{{ today_at(states(entity)).strftime('%-I:%M %p') | lower }}"
          - type: custom:mushroom-template-card
            entity: switch.s25d_schedule_2_enabled
            primary_info: none
            secondary_info: none
            icon: |
              {% if is_state(entity, 'on') %}
                mdi:paw
              {% else %}
                mdi:paw-off-outline
              {% endif %}
            color: |
              {% if is_state(entity, 'on') %}
                primary
              {% else %}
                disabled
              {% endif %}
          - type: custom:mushroom-template-card
            entity: number.s25d_schedule_2_portions
            icon: |
              {% if is_state('switch.s25d_schedule_2_enabled', 'on') %}
                mdi:food
              {% else %}
                mdi:food-off-outline
              {% endif %}
            color: |
              {% if is_state('switch.s25d_schedule_2_enabled', 'on') %}
                primary
              {% else %}
                disabled
              {% endif %}
            badge_text: "{{ states(entity) }}"
            badge_color: |
              {% if is_state('switch.s25d_schedule_2_enabled', 'on') %}
                accent
              {% else %}
                disabled
              {% endif %}
          - type: custom:mushroom-template-card
            entity: time.s25d_schedule_2_time
            icon: |
              {% if is_state('switch.s25d_schedule_2_enabled', 'on') %}
                mdi:clock
              {% else %}
                mdi:clock-outline
              {% endif %}
            color: |
              {% if is_state('switch.s25d_schedule_2_enabled', 'on') %}
                primary
              {% else %}
                disabled
              {% endif %}
            primary: "{{ today_at(states(entity)).strftime('%-I:%M %p') | lower }}"
          - type: custom:mushroom-template-card
            entity: switch.s25d_schedule_3_enabled
            primary_info: none
            secondary_info: none
            icon: |
              {% if is_state(entity, 'on') %}
                mdi:paw
              {% else %}
                mdi:paw-off-outline
              {% endif %}
            color: |
              {% if is_state(entity, 'on') %}
                primary
              {% else %}
                disabled
              {% endif %}
          - type: custom:mushroom-template-card
            entity: number.s25d_schedule_3_portions
            icon: |
              {% if is_state('switch.s25d_schedule_3_enabled', 'on') %}
                mdi:food
              {% else %}
                mdi:food-off-outline
              {% endif %}
            color: |
              {% if is_state('switch.s25d_schedule_3_enabled', 'on') %}
                primary
              {% else %}
                disabled
              {% endif %}
            badge_text: "{{ states(entity) }}"
            badge_color: |
              {% if is_state('switch.s25d_schedule_3_enabled', 'on') %}
                accent
              {% else %}
                disabled
              {% endif %}
          - type: custom:mushroom-template-card
            entity: time.s25d_schedule_3_time
            icon: |
              {% if is_state('switch.s25d_schedule_3_enabled', 'on') %}
                mdi:clock
              {% else %}
                mdi:clock-outline
              {% endif %}
            color: |
              {% if is_state('switch.s25d_schedule_3_enabled', 'on') %}
                primary
              {% else %}
                disabled
              {% endif %}
            primary: "{{ today_at(states(entity)).strftime('%-I:%M %p') | lower }}"
          - type: custom:mushroom-template-card
            entity: switch.s25d_schedule_4_enabled
            primary_info: none
            secondary_info: none
            icon: |
              {% if is_state(entity, 'on') %}
                mdi:paw
              {% else %}
                mdi:paw-off-outline
              {% endif %}
            color: |
              {% if is_state(entity, 'on') %}
                primary
              {% else %}
                disabled
              {% endif %}
          - type: custom:mushroom-template-card
            entity: number.s25d_schedule_4_portions
            icon: |
              {% if is_state('switch.s25d_schedule_4_enabled', 'on') %}
                mdi:food
              {% else %}
                mdi:food-off-outline
              {% endif %}
            color: |
              {% if is_state('switch.s25d_schedule_4_enabled', 'on') %}
                primary
              {% else %}
                disabled
              {% endif %}
            badge_text: "{{ states(entity) }}"
            badge_color: |
              {% if is_state('switch.s25d_schedule_4_enabled', 'on') %}
                accent
              {% else %}
                disabled
              {% endif %}
          - type: custom:mushroom-template-card
            entity: time.s25d_schedule_4_time
            icon: |
              {% if is_state('switch.s25d_schedule_4_enabled', 'on') %}
                mdi:clock
              {% else %}
                mdi:clock-outline
              {% endif %}
            color: |
              {% if is_state('switch.s25d_schedule_4_enabled', 'on') %}
                primary
              {% else %}
                disabled
              {% endif %}
            primary: "{{ today_at(states(entity)).strftime('%-I:%M %p') | lower }}"
          - type: custom:mushroom-template-card
            entity: switch.s25d_schedule_5_enabled
            primary_info: none
            secondary_info: none
            icon: |
              {% if is_state(entity, 'on') %}
                mdi:paw
              {% else %}
                mdi:paw-off-outline
              {% endif %}
            color: |
              {% if is_state(entity, 'on') %}
                primary
              {% else %}
                disabled
              {% endif %}
          - type: custom:mushroom-template-card
            entity: number.s25d_schedule_5_portions
            icon: |
              {% if is_state('switch.s25d_schedule_5_enabled', 'on') %}
                mdi:food
              {% else %}
                mdi:food-off-outline
              {% endif %}
            color: |
              {% if is_state('switch.s25d_schedule_5_enabled', 'on') %}
                primary
              {% else %}
                disabled
              {% endif %}
            badge_text: "{{ states(entity) }}"
            badge_color: |
              {% if is_state('switch.s25d_schedule_5_enabled', 'on') %}
                accent
              {% else %}
                disabled
              {% endif %}
          - type: custom:mushroom-template-card
            entity: time.s25d_schedule_5_time
            icon: |
              {% if is_state('switch.s25d_schedule_5_enabled', 'on') %}
                mdi:clock
              {% else %}
                mdi:clock-outline
              {% endif %}
            color: |
              {% if is_state('switch.s25d_schedule_5_enabled', 'on') %}
                primary
              {% else %}
                disabled
              {% endif %}
            primary: "{{ today_at(states(entity)).strftime('%-I:%M %p') | lower }}"
          - type: custom:mushroom-template-card
            entity: switch.s25d_schedule_6_enabled
            primary_info: none
            secondary_info: none
            icon: |
              {% if is_state(entity, 'on') %}
                mdi:paw
              {% else %}
                mdi:paw-off-outline
              {% endif %}
            color: |
              {% if is_state(entity, 'on') %}
                primary
              {% else %}
                disabled
              {% endif %}
          - type: custom:mushroom-template-card
            entity: number.s25d_schedule_6_portions
            icon: |
              {% if is_state('switch.s25d_schedule_6_enabled', 'on') %}
                mdi:food
              {% else %}
                mdi:food-off-outline
              {% endif %}
            color: |
              {% if is_state('switch.s25d_schedule_6_enabled', 'on') %}
                primary
              {% else %}
                disabled
              {% endif %}
            badge_text: "{{ states(entity) }}"
            badge_color: |
              {% if is_state('switch.s25d_schedule_6_enabled', 'on') %}
                accent
              {% else %}
                disabled
              {% endif %}
          - type: custom:mushroom-template-card
            entity: time.s25d_schedule_6_time
            icon: |
              {% if is_state('switch.s25d_schedule_6_enabled', 'on') %}
                mdi:clock
              {% else %}
                mdi:clock-outline
              {% endif %}
            color: |
              {% if is_state('switch.s25d_schedule_6_enabled', 'on') %}
                primary
              {% else %}
                disabled
              {% endif %}
            primary: "{{ today_at(states(entity)).strftime('%-I:%M %p') | lower }}"
  - type: vertical-stack
    cards:
      - type: custom:bubble-card
        card_type: pop-up
        hash: "#feeding-settings"
        button_type: name
        sub_button:
          main: []
          bottom: []
        show_header: false
      - type: custom:mushroom-title-card
        title: Device Settings
      - type: custom:layout-card
        layout_type: custom:grid-layout
        layout:
          grid-template-columns: 35% 65%
          padding: 0px
          margin: 0px
        cards:
          - type: custom:mushroom-entity-card
            entity: switch.s25d_button_lockout
            layout: horizontal
            name: Child Lock
          - type: custom:mushroom-select-card
            entity: select.s25d_meal_call_sound
            name: Meal Call
            layout: horizontal
            secondary_info: none
      - type: custom:mushroom-title-card
        title: Device Status
      - type: custom:layout-card
        layout_type: custom:grid-layout
        layout:
          grid-template-columns: 50% 50%
          padding: 0px
          margin: 0px
        cards:
          - type: custom:mushroom-entity-card
            entity: binary_sensor.s25d_battery_backup
            name: Battery Backup
          - type: custom:mushroom-entity-card
            entity: sensor.s25d_battery
            name: Battery Level
            icon_color: green
          - type: custom:mushroom-entity-card
            entity: sensor.s25d_desiccant_expiry
            name: Dessicant Remaining
          - type: custom:mushroom-entity-card
            entity: binary_sensor.s25d_online
            name: Online
title: Kibble
```

</details>

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Go to "Integrations"
3. Search for "HGSmart Pet Feeder"
4. Click "Download"
5. Restart Home Assistant

### Configuration

1. Go to Settings > Devices & Services
2. Click "Add Integration"
3. Search for "HGSmart Pet Feeder"
4. Enter your HGSmart account credentials
5. Configure the update interval (default: 15 seconds)


## Questions

### Is this vibe-coded?

Quite a bit, yes. I'm sorry about that, but I have little expertise with Python and HASS API. Most of my work was reverse-engineer the API from the Flutter-based Android app and review the code Claude wrote.

### How does the authentication works?

HG APIs are authenticated via a OAuth2-looking protocol: username+password are exchanged once for an access token and refresh token, and whenever the access token expires, the refresh token is used to get a new one. 

Note that the password is not stored, so you might have to re-authenticate once in a long while. Not sure when, as my refresh token is yet to expire (if ever).

### Can I trust this integration?

As much as you can trust any random repository on the internet, I guess. I wrote it for me, and it works fine. I swear don't care about stealing your credentials and feeding your cats when you are not looking.

### Is S25T the only supported model?

Yes, but the API looks generic enough to work with more models with little to no changes. Let me know you'd like to help me add support for more models.

## License

This integration is provided as-is for personal use. It is not affiliated with or endorsed by HoneyGuardian/HoneyGuaridan/HoneyGuaridian/HGSmart.

