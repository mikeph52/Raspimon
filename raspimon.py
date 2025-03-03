#!/usr/bin/env python

#Function list
# Main functions:

def clr():
    import subprocess
    subprocess.run(['clear'])

def main():
 clr()
 print("##########################################")
 print("Raspimon 0.28 for Raspberry Pi OS by mikeph_\n")
 print("This is a system monitor program written for Raspberry pi 4.\nVery Nice:-O\n")
 print("[Select bellow:]\na)Temperature,b)CPU load,c)Disk info,d)Network info,e)Devices connected via SSH,f)GPIO status,g)Raspberry Pi config menu!NEEDS SUDO PRIVILEGES!.\n(Type the letter ex. a)")
 print("for more documentation type 'help'.")
 print("##########################################")
 print("\033[1;31;40mTo exit, type 'exit'.\033[1;37;40m")
 x = input()
 if x == 'a':
        tempr()
 if x == 'b':
        cpuld()
 if x == 'c':
        disk()
 if x == 'd':
        ipc()
 if x == 'e':
        who()
 if x == 'f':
        gpio_status()
 if x == 'g':
        raspconf()
 if x == 'help':
        help()
 if x == 'exit':
     exit()

def escape():
    print("\033[1;31;40mPress 'a' to return to menu, 'b' to exit:\033[1;37;40m")
    y = input()
    if y == 'a':
        main()
    else:
        exit() 


#Features functions:

def tempr():
    clr()
    import time
    for i in range(10):
       print('Temperature:')
       import subprocess
       subprocess.run(['vcgencmd', 'measure_temp'])
       time.sleep(2)
    escape()

def cpuld():
    clr()
    print("To stop the process press 'q'.")
    import time
    time.sleep(1)
    import subprocess
    subprocess.run(['top', '-i'])
    escape()

def disk():
    clr()
    import subprocess
    subprocess.run(['lsblk'])
    escape()

def ipc():
    clr()
    import subprocess
    #subprocess.run(['ip', '-c', 'a'])
    #print("-----------------------------------------------------------------")
    subprocess.run(['hostname'])
    subprocess.run(['hostname', '-I'])
    subprocess.run(['grep', '"nameserver"', '/etc/resolv.conf'])
    subprocess.run(['systemctl', 'status', 'apache2'])

    escape()
    
def help():
    clr()
    text = open("raspimondoc.txt",'r')
    print(text.read())
    escape()

def who():
    clr()
    import subprocess
    subprocess.run(['w'])
    escape()

def gpio_status():
    clr()
    import subprocess
    subprocess.run(['gpio','readall'])
    escape()

def raspconf():
    clr()
    import subprocess
    subprocess.run(['raspi-config'])
    escape()

#main program

main()
