
;start change plate

G28 Y
G1 Y262 F2000

M400 ; wait all motion done
M17 Z0.4 ; lower z motor current to reduce impact if there is something in the bottom

G91
G1 Z-35 F1200
G380 S2 Z95

G1 Z-25
G380 S2 Z90

G1 Z-25
G380 S2 Z90

G1 Z-30
G380 S2 Z95

G1 Z-5 
G90
 
G4 P2000

G1 Y15 F800
G1 Y150 F1200

;This line controls how much distance the tail of the reel hangs. The smaller the value, the less distance it hangs.
G1 Y40 F1000 

;The platform ensures that new plate can be added properly even when no plate are available.
;G1 Y260 F3000
G380 S2 Y264 F2000

G91;
G1 Z-70 F1200
G90;

;
G1 Y200 F10000

G1 Y25 F4000

;Avoiding the use of the G380 command is to prevent failure in reaching the correct position.
G1 Y-1 F20000
;G380 S3 Y-1 F1200

G4 P500

G28 Y

;start  Shake-triggered plate replacement
G1 Y1 F100
G1 Y0 F20000
G4 P500
G1 Y1.5 F100
G1 Y0 F20000
G4 P500
G1 Y2 F100
G1 Y0 F20000
G4 P500
G1 Y2.5 F100
G1 Y0 F20000
G4 P500
G1 Y2.5 F100
G1 Y0 F10000
G4 P500
;G1 Y3.5 F100
;G1 Y0 F5000
;end   Shake-triggered plate replacement

G1 Y235 F1200
G1 Y110 F5000
G1 Y200 F1200
;G1 Y266 F1500
G380 S2 Y264 F2000

;After retracting the build plate to the frontmost position, dwell for 1 second.
G4 P1000  

G1 Y25 F4000
;G1 Y-2 F20000
G1 Y-2 F1000

G4 P500

;start  Shake-triggered plate replacement
G1 Y1 F100
G1 Y0 F20000
G4 P500
G1 Y1.5 F100
G1 Y0 F20000
G4 P500
G1 Y2 F100
G1 Y0 F20000
G4 P500
G1 Y2.5 F100
G1 Y0 F20000
G4 P500
G1 Y2.5 F100
G1 Y0 F10000
G4 P500


;end   Shake-triggered plate replacement

;move to front
G1 Y262 F1500

G1 Y120 F2000

;end change plate