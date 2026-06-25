# Alpine A290 Home-Assistant Custom Dashboard User Guide

**Version: V02-0126**

This user guide is designed to give you an overview of how the card works and what you see represented on the UI. It is assumed that you have already installed the card, as per the instructions in the main Readme installation document.

## Overview

The card is split into 3 columns, each representing different elements of the car and information pertinent to it.

### Column 1. - Vehicle Status

This column is comprised of an image that resembles the exact model and trim level of your car, underneath which you will see twelve sensors.

<!-- screenshot placeholder (image1) — add a current Alpine A290 screenshot here -->

1.  Dynamic Charge Indicator

> When you connect a charging plug to the car or you run a test charge, the main car image in the “Vehicle Status” section, is automatically replaced by a charge indicator, that dynamically displays the charge level (Current SOC) and the charge limits set (Min/Target SOC). If a real charge session commences or you run a test charge, the image will then animate to show charge progress and “Current SOC” will be replaced by “Charging”. Note that the SOC will not increase during a test charge. Once the test charge has ended, if no plug is connected, the card disappears and the main car image replaces it once more. When a real charge ends, the card will remain visible until you disconnect the plug.
>
> <!-- screenshot placeholder (image2) — add a current Alpine A290 screenshot here -->

1.  Battery Level

> <!-- screenshot placeholder (image3) — add a current Alpine A290 screenshot here -->
>
> This sensor displays the current battery level of the car. When the level is above 30% the icon will turn green, when above 15% the icon will turn yellow and if below 15% it will turn red.

2.  Battery Energy

> <!-- screenshot placeholder (image4) — add a current Alpine A290 screenshot here -->
>
> This sensor displays the available energy of the battery, expressed in kWh. When the level is above 30kWh the icon will turn green, when above 15kWh the icon will turn yellow and if below 15kWh, it will turn red.

3.  Estimated Range

> <!-- screenshot placeholder (image5) — add a current Alpine A290 screenshot here -->
>
> This sensor displays the estimated range of the car, based on your driving habits from the last 70km travelled and is calculated directly from the Renault API. If the estimate is \>100 then the icon will turn green. If \>50 it will turn orange and if \<50 it will turn red.

4.  Odometer

> <!-- screenshot placeholder (image6) — add a current Alpine A290 screenshot here -->
>
> This sensor displays your current odometer reading. Depending on your setup, it can be configured to show KM or Miles.

5.  Charger Plug

> <!-- screenshot placeholder (image7) — add a current Alpine A290 screenshot here -->
>
> This sensor can display the status of your charger plug. When disconnected (as shown above) the icon will be greyed out. When connected the icon will change to yellow and the state will show “Connected” Additionally it can also display faults as a suffix to the state:
>
> (A): Authorization problem with the server
>
> (D): Derived state, using cached data
>
> (S): Stale data.
>
> If a command prompt is detected during a log-in attempt it will display
>
> “Unknown (CLI)”
>
> During a test charge the state will be displayed as:
>
> “(Test) Connected”
>
> In this instance the icon will change to a “flask” and pulse red, during the 5 min test.

6.  Charger Status

> <!-- screenshot placeholder (image8) — add a current Alpine A290 screenshot here -->
>
> This sensor displays the charging status of your car. When “Not Charging” (as shown above) or “Charge Ended” the icon will be greyed out. When you connect a plug to the car, the icon will change to yellow and begin to pulse. The state will change to “On Hold”. Once charging commences, the icon will turn orange, continue to pulse and the state will change to “In Progress”. Once charging ends, the state will change to “Charge Ended” and the icon will return to grey. Additionally it can also display fault states as a suffix:
>
> (A): Authorization problem with the server
>
> (D): Derived state, using cached data
>
> (S): Stale data.
>
> If a command prompt is detected during a log-in attempt it will display
>
> “Unknown (CLI)”
>
> During a test charge the state will be displayed as:
>
> “(Test) In Progress”
>
> In this instance the icon will change to a “flask” and pulse red, during the 5 min test.

7.  Charging Rate

> <!-- screenshot placeholder (image9) — add a current Alpine A290 screenshot here -->
>
> This sensor displays your current charging rate, as exposed by your Home Assistant EV charger integration. If no charge is taking place the icon will be greyed out and the value will read as 0kw. Once charging is detected (\>0.2kw) the icon will turn orange and begin to pulse. The state will show the value in kw at the current time, being output by the charger. In the case of Public charging, the sensor continually samples time elapsed and SOC gained to obtain a value for the “Last Charge” section of the card. The state will be displayed as “Public”. Once charging stops, the icon will return to grey and the value will once again read 0kw.
>
> During a test charge the state will be displayed as:
>
> “(Test) 7.2kw”
>
> In this instance the icon will change to a “flask” and pulse red, during the 5 min test

8.  Time Left

> <!-- screenshot placeholder (image10) — add a current Alpine A290 screenshot here -->
>
> This sensor displays the time remaining for the current charger and is derived directly from the API. Renault has a bug whereby the sensor can stick at 3 or 4 mins at the end of the charge. The logic detects this and zeroes the value after a set period. When there is no charge taking place you will see the icon is greyed out and the state shows “Not Charging”. Once a charge commences, the time left will be displayed in hrs and mins. The icon colour logic is:
>
> \>60 mins remaining: red
>
> \>30 mins remaining: orange
>
> \> 15 mins remaining: yellow
>
> \>5 mins remaining: green
>
> During a test charge the state will be displayed as:
>
> “(Test) 5 min”
>
> In this instance the icon will change to a “flask” and pulse red, during the 5 min test

9.  Charging Flap

> <!-- screenshot placeholder (image11) — add a current Alpine A290 screenshot here -->
>
> This sensor displays the state of the charging flap. When closed the “Locked Check” icon is shown in green and the state will display “Closed” as shown above. When the flap is open the icon will change to “Unlocked” and turn yellow. Moreover, if it senses that it is open and a plug is attached the state will show:
>
> “Open: Plugged In”
>
> If it does not detect a plug, it will show:
>
> “Open: No Plug!” to warn you and the icon will turn red and wiggle.
>
> In addition it is also capable or reporting fault states
>
> Undetermined (A): Authorisation issue with the server
>
> Undetermined (S): Stale data
>
> Unknown (CLI): Command prompt detected during login
>
> In these fault states the icon will change to a “Help” symbol
>
> During a test charge the state will be displayed as:
>
> “(Test) Open”
>
> In this instance the icon will change to a “flask” and pulse red, during the 5 min test
>
> \(10\) HVAC Status
>
> <!-- screenshot placeholder (image13) — add a current Alpine A290 screenshot here -->
>
> This sensor simply displays if the HVAC is running on the car. When off the icon is greyed out and shows fan-off. When on, the icon changes to fan-on, turns yellow and spins. If you switch on HVAC via the Kelec or My Alpine app, you will see it change. Additionally you can manually run HVAC from the card’s “Remote Control” section.
>
> \(11\) HVAC Threshold
>
> <!-- screenshot placeholder (image14) — add a current Alpine A290 screenshot here -->
>
> This sensor displays the HVAC threshold of your car. The value is derived directly from the API, and is the battery’s SoC value, below which you CANNOT start HVAC. To warn you, if the battery’s SOC drops below 15%, the icon turns red.
>
> <u>Column 2: “Last Activity”</u>
>
> <!-- screenshot placeholder (image15) — add a current Alpine A290 screenshot here -->
>
> This section is self-explanatory and shows the last reported activity for each sensor and is driven wholly by the API. You may see some lag in these values but this is fundamentally down to how often Renault polls the data from the car and/or if the car is in a “sleep” state, not recently driven or no activity is detected.
>
> <u>Column 2: “Current Location”</u>
>
> The map card (not shown here) displays the present location of your car. This is updated at last key off and will refresh when you next drive the car. Any lag in location data is purely down to the API.
>
> <u>Column 2: “Climate/Charging Presets”</u>
>
> <!-- screenshot placeholder (image16) — add a current Alpine A290 screenshot here -->
>
> This section is made up of 8 sensors. The values represent those you have currently set in the My Alpine app or that you made from the car itself

1.  Desired Temp: displays the pre-conditioning temperature (target) you require when running HVAC.

2.  Steering Wheel: displays whether you want heat on/off when running HVAC

3.  Passenger/Driver’s seat – as for steering wheel

4.  Min SOC: minimum charge you set for charging sessions

5.  Max SOC: maximum charge you set for charging sessions

6.  Start Charge: time you set to start/accept a charge

7.  Stop Charge: time you set to end/decline a charge

> <u>Column 3: “Remote Control”</u>
>
> <!-- screenshot placeholder (image17) — add a current Alpine A290 screenshot here -->
>
> This section works in much the same way as the My Alpine app Note that “Start Charging” and “Stop Charging” are currently disabled and greyed out, as they are not yet functional in the API for the Alpine A290 (at least not in a useful way)
>
> Each of these buttons fires an automation that runs scripts. Notifications will appear in Home Assistant (and on your mobile if configured correctly) to advise you as to their progress/completion or failure to execute.
>
> HVAC Start: can often lag, depending on the state of the car (sleep mode) but will usually wake the car up and run HVAC within a couple of minutes. You will see this reflected by the spinning yellow icon next to the HVAC Status sensor discussed earlier.
>
> HVAC Stop: Can and often does NOT work well, due wholly to the API and is a limitation that is a Renault server side problem. However, if the API is playing ball, then it does stop the HVAC running within a minute or so. Note that in any case HVAC will timeout after 15 mins of run time by design.
>
> Beep Horn: when pressed will beep your car’s horn 3 times in succession
>
> Flash Lights: when pressed will flash your car’s headlights 3 times in quick succession.
>
> <u>Column 3: “Last Charge”</u>
>
> <!-- screenshot placeholder (image18) — add a current Alpine A290 screenshot here -->
>
> This section is made up of 15 sensors, most of which are self explanatory such as start/end soc, total etc.
>
> When the start of a charge is detected, the Start SOC (78% as shown in the example above) will reflect the value captured at that moment, similarly the same applies to the start energy value (40.6 kWh as shown in the example above and the Initial power from the charger 6.0 kw). ALL the remaining sensor’s icons will turn grey and their states will show “Wait…” for the duration of the charge.
>
> A notification will also appear in Home Assistant confirming the value captured at the start of the charge. Once the end of charging is detected, the remaining sensor’s icons will return to the colours depicted above and their states will display the charge session data captured. A notification will also appear, with the end of charge captured data
>
> In the case of
>
> “Initial”
>
> This value represents the value the Home Charger initially output to the car at the moment charging began.
>
> “Avg”
>
> This value represents the average value of power delivered by the charger over the duration of the charge.
>
> If public charging “Initial” and “Avg” will be computed averages, based on SOC change and Duration, if these cannot be calculated both will display ”Public” as their state.
>
> “Uplift”
>
> This is derived from the API and differs from energy gained, (which simply compares start/end energy levels to display a value, in the example above 11.4 kWh). The uplift figure is often referred to by Renault as “energy stored” and is the amount calculated as delivered to the car’s battery taking into account BMS rate limiting/capping and any losses, hence why it differs from energy gained. You will see the same figures reflected in the charging history section of both the My Alpine and Kelec apps.
>
> “Type”
>
> This sensor looks at the duration of the charge and the amount of energy gained in that time, to derive one of five values that it can display:
>
> ‘Rapid DC’, ‘Fast DC’, ‘Fast AC’ or ‘Slow AC’
>
> If it cannot determine a value (occasionally this can occur when public charging), it will display “Not Rated” and its icon will change to an alert symbol and turn red.
>
> Depending on the calculation it makes, will denote the icon colour:
>
> ‘Rapid DC’, green
>
> ‘Fast DC’, orange
>
> ‘Fast AC’ yellow
>
> ‘Slow AC’, red
>
> “Status”
>
> This sensor denotes the end of charge session status and is reported by the API. It should show “Completed” if no errors were detected during the charge or “Failed” if an error was detected or “N/A” if it cannot determine the end of charge status.
>
> “Charge Date”
>
> Shows the date the start of charge was captured
>
> <u>Column 3: “Update/Test/Errors”</u>
>
> This section is made up of seven buttons some of which are hidden and only appear conditionally
>
> “Soft Update”
>
> This button forces an update of the RAW sensors by calling the Renault API. When you click on it, a pop-up dialog appears explaining what a soft update entails and how it works, as well as a warning. To execute the update a button will appear in the pop-up for you to action the update. Typically you might use this if you saw any of the sensors reporting (S) stale data or (D) derived data from cache values.
>
> “Run Test Charge”
>
> This button will launch a 5 min test charge to prove the logic of the system. Note that it is inhibited during a real charge session or once a test charge has started. When you start a test charge the “Last Charge” block of sensors is replaced by another “Test Charge” block of sensors. All will be greyed out with flask icons, pulsing in red. Similarly, over in the “Vehicle Status” sensor block, the Charger Plug, Charging Status, Charging Rate, Time Left and Charger Flap sensors will also change icons to a flask and pulse red with their test states displayed (as discussed earlier in this user guide)
>
> Whilst a test charge is running the button will change to an animated egg timer icon in green, showing the time remaining until the test charge completes. At the end of the test charge, all icons will return to their normal type and colour. The Vehicle Status sensors, will display their original values and the Test Charge values will continue to be displayed for one minute, during which time the egg timer button is replaced by an “Auto Reset” button that counts down for 1 minute. At the end of this minute the “Test Charge” card will be replaced by the “Last Charge” card with all the last charge data intact and the Run Test Charge button will reappear.
>
> Note that values generated during the test are partly captured and partly fixed notional ones, simply to exercise the logic and ensure everything is working properly, in anticipation of a real charge session. You do NOT need to run the test ordinarily.
>
> Three further buttons can appear under certain circimstances:
>
> “CLI Prompt”, “Auth Failure” and “Stale Data”
>
> When an error is detected their state will change to “on” prompting them to become visible. Their icons will be in red and pulsing, denoting there is a problem. You can click on each of them, which will bring up a pop-up daialog box explaining the cause behind the error and how to deal with it.
>
> CLI Prompt: denotes that on attempting to retrieve data from Renault’s API to update the RAW sensors from an endpoint, the CLI prompted for interaction, usually a username or locale. Normally automation “8” handles this automatically for you, but it might be that you need to run it manually or login into the API yourself via the “Advanced SSH & Web Terminal” in Home Assistant to clear the fault.
>
> Auth Failure: denotes that authorisation has failed, possibly caused by a corrupt credentials file or some other issue. Again this is usually handled by the automations, but you might need to check your credentials file or reauthenticate yourself via the Terminal.
>
> Stale Data: This is usually caused by the API lagging for whatever reason, in updating the car’s data. Automations monitor how “fresh” the data is between polling intervals and if they appear “stuck” for a pre-determined time, then it prompts a refresh and invokes this condition causing the button to appear.
>
> <u>Latest Update</u>
>
> When an active charge session takes place, the main car image in the “Vehicle Status” section, is automatically replaced by a charge indicator, that dynamically displays the charge level (Current SOC) and the charge limits set (Min/Target SOC). Once charging has ended, the card disappears and the main car image replaces it.
>
> <!-- screenshot placeholder (image2) — add a current Alpine A290 screenshot here -->
>
> Well that is just about it! I hope you enjoy using the card and as always, if you spot any errors/bugs please report them to me. My email address can be found in the installation guide.
>
> Regards
>
> Rod
