# Telecommand Data Link Layer Protocol (TC)

## Valid Packets
For these cases, the channel parameters are:
```
Segment Header Present            = False
Frame Error Control Field Present = True
Security Header Length            = 0
Security Trailer Length           = 0
Is Return Link (CLCW)             = False
```

**ad-fecf.bin**
* **Hex:** `01230C0A0A54455354DA81`
* **Parse Tree:**
```
  Transfer Frame Version Number    0
                  Spacecraft ID    291
             Virtual Channel ID    3
    Virtual Channel Frame Count    10
                    Bypass Flag    false
           Control Command Flag    false
                         Length    11
                           FECF    DA81
                     Frame Type    AD
                     Idle Frame    false
                     Data Field    54455354
```

**bc-fecf.bin**
* **Hex:** `3123140700005C8C`
* **Parse Tree:**
```
  Transfer Frame Version Number    0
                  Spacecraft ID    291
             Virtual Channel ID    5
    Virtual Channel Frame Count    0
                    Bypass Flag    true
           Control Command Flag    true
                         Length    8
                           FECF    5C8C
                     Frame Type    BC
             BC Control Command    UNLOCK
                     Idle Frame    false
                     Data Field    00
```

**bd-fecf.bin**
* **Hex:** `21230C0700FF4C8C`
* **Parse Tree:**
```
  Transfer Frame Version Number    0
                  Spacecraft ID    291
             Virtual Channel ID    3
    Virtual Channel Frame Count    0
                    Bypass Flag    true
           Control Command Flag    false
                         Length    8
                           FECF    4C8C
                     Frame Type    BD
                     Idle Frame    false
                     Data Field    FF
```