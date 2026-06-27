# Dashboards on mobile

The bundled Alpine A290 dashboards are designed for phones and **automatically tested** on
every change: a CI job renders them in a real Home Assistant across the top mobile device
sizes and fails the build if any text is truncated or any card fails to render. Typography is
the same on every screen (no font/size changes between phone and desktop); labels and headings
**wrap on clean word breaks** rather than getting cut off.

Devices validated each run: **iPhone 15 Pro Max, iPhone 15 Pro, iPhone 15, iPhone SE, Pixel 8,
Pixel 7a, Galaxy S24, Galaxy S23, Galaxy A54, and a 360 px narrow Android bound.**

## Standard dashboard

| iPhone 15 Pro (393 px) | Galaxy S24 (360 px) |
| --- | --- |
| ![Standard dashboard on iPhone 15 Pro](screenshots/standard-iphone-15-pro.png) | ![Standard dashboard on Galaxy S24](screenshots/standard-galaxy-s24.png) |

## Bubble dashboard

| iPhone 15 Pro (393 px) | Galaxy S24 (360 px) |
| --- | --- |
| ![Bubble dashboard on iPhone 15 Pro](screenshots/bubble-iphone-15-pro.png) | ![Bubble dashboard on Galaxy S24](screenshots/bubble-galaxy-s24.png) |

> Screenshots are produced by the **UI Tests** workflow ([`ui-tests/`](../ui-tests/)) with
> representative sample data; the full per-device set is uploaded as a build artifact on every
> run.
