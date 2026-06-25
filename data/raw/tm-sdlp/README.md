# Telemetry Space Data Link Layer Protocol (TM-SDLP)

## Valid Packets
For these cases, the channel parameters are:
```
Segment Header Present            = False
Frame Error Control Field Present = True
Security Header Length            = 0
Security Trailer Length           = 0
```

**1.bin**
* **Hex:** `12330A05180044415441C0000100F61E`
* **Transfer Frame Length (bytes):** 16
* **Parse Tree:**
```
  Transfer Frame Version Number    0
                  Spacecraft ID    291
             Virtual Channel ID    1
     Master Channel Frame Count    10
    Virtual Channel Frame Count    5
              Packet Order Flag    false
       Secondary Header Present    false
           Synchronisation Flag    false
      Segment Length Identifier    3
                            OCF    C0000100
                           FECF    F61E
                 Frame Validity    OK
                     Idle Frame    false
             No start of packet    false
           First Header Pointer    0
                     Data Field    44415441
```

**2.bin**
* **Hex:** `123705021800BEEF00000001711B`
* **Transfer Frame Length (bytes):** 14
* **Parse Tree:**
```
  Transfer Frame Version Number    0
                  Spacecraft ID    291
             Virtual Channel ID    3
     Master Channel Frame Count    5
    Virtual Channel Frame Count    2
              Packet Order Flag    false
       Secondary Header Present    false
           Synchronisation Flag    false
      Segment Length Identifier    3
                            OCF    00000001
                           FECF    711B
                 Frame Validity    OK
                     Idle Frame    false
             No start of packet    false
           First Header Pointer    0
                     Data Field    BEEF
```

**3.bin**
* **Hex:** `12400603180411223344554091`
* **Transfer Frame Length (bytes):** 13
* **Parse Tree:**
```
  Transfer Frame Version Number    0
                  Spacecraft ID    292
             Virtual Channel ID    0
     Master Channel Frame Count    6
    Virtual Channel Frame Count    3
              Packet Order Flag    false
       Secondary Header Present    false
           Synchronisation Flag    false
      Segment Length Identifier    3
                           FECF    4091
                 Frame Validity    OK
                     Idle Frame    false
             No start of packet    false
           First Header Pointer    4
                     Data Field    1122334455
```