# HGSmart Pet Feeder Integration for Home Assistant

A custom Home Assistant integration for Honey Guardian/Guaridan/Guaridian S25T Pet Feeder, providing a replacement for the (horrid) HGSmart app.

This integration was developed through reverse engineering of the HGSmart Android application and provides access to feeding schedules, manual feeding, food level monitoring, and device status.

<img width="458" height="228" alt="immagine" src="https://github.com/user-attachments/assets/99edf000-6a98-454f-a1bf-c3c767541198" />

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

## Features

- Manual feeding button with configurable portions
- Access to the programmable feeding schedules of the device
- Real-time food level monitoring
- Desiccant expiration tracking and reset
- Service for custom integrations / schedules (but neeed to trust the HGSmart APIs and the pet feeder Wi-Fi connectivity)

## Exposed entities
<img width="518" height="1328" alt="immagine" src="https://github.com/user-attachments/assets/608edcc3-6b57-440a-bbe1-11276db96c49" />

## Exposed sensors
<img width="518" height="271" alt="immagine" src="https://github.com/user-attachments/assets/05adaade-49a7-4e73-a64e-40627460e0a4" />



## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Go to "Integrations"
3. Click the three dots menu in the top right
4. Select "Custom repositories"
5. Add the repository URL: `https://github.com/Serjlee/hacs_hgsmart-pet-feeder`
6. Select category: "Integration"
7. Click "Add"
8. Click "Install" on the HGSmart Pet Feeder card
9. Restart Home Assistant

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

