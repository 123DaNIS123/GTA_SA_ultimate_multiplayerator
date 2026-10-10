# GTA_SA_ultimate_multiplayerator
GTA SA .asi mod that makes your GTA SA single-player be playable as split-screen or as online multiplayer.
Mod in development process..

INSTALLATION IS SIMPLE:
JUST MOVE ALL DOWNLOADED MODS FILES AND REQUIRED DEPENDENCIES TO GAME FOLDER where gta_sa.exe is. (clean 1.0 US .exe)

Recommendations for all mods:
  - use ASI loader to make these mods work, recommend Silent's ASI Loader:
https://www.gtagarage.com/mods/download.php?f=37262
  - use Silent's Patch to fix most common GTA SA game issues such as low frame rate limit, some game bugs etc. Because tested and developed with Silent's Patch:
https://github.com/CookiePLMonster/SilentPatch/releases/download/1.1-BUILD34.1-SA/SilentPatchSA.zip
  - use GInput for controller support:
https://silent.rockstarvision.com/uploads/GInputSA.zip

1)
_________________________________________________________________________________________________________________________________________________________________________
Split-Screen Multiplayer:
What does:
  * converts single-player GTA SA to split-screen multiplayer in one PC.
    // in SplitScren.ini file: check controls for different actions such as:
            1. drop weapon/ammo, drop money
            2. revive player <---- you can adjust the times in .ini file (there are 2 knocked out causes: nearly busted and nearly wasted)
            3. sit as passenger, hijack player <------ hold same button - sit as passenger
    // if you will use GTA5LikeControls: for drops use RB instead of LB, use G instead of Tab etc. You can look for the changes in .ini file. Other buttons instructions will pop-up
installation:
  * move everyting from GTASASplitScreenCOOP to the game folder. check in both .ini files Enabled=1. Split-screen version!


__________________________________________________________________________________________________________________________________________________________________________
GTA5LikeControls:
What does:
  * makes controls more comfortable, especially when using controllers.
  * make constrols as in GTA5:
           - YOU CAN use weapon selection wheel using LB as in GTA 5
           - COMFORABLE controls for aiming and shooting as in GTA 5  
installation:
  * move gta5likecontrols.asi and gta5likecontrols.ini from GTASASplitScreenCOOP to the game folder and check in both .ini files Enabled=1


2)
_____________________________________________________________________________________________________________________________________________
GTA Zombie Andreas specific split-screen:
disable or remove from the game folder:
1) UltimateMultiplayeratorSplitScreen.asi, UltimateMultiplayeratorSplitScreen.ini
2) gta5likecontrols.asi, gta5likecontrols.ini
or Enable=0 in UltimateMultiplayeratorSplitScreen.ini and gta5likecontrols.ini.
Enalbe COOP in in-game settings (you do it in gamemode selection process, others -> COOP enable)
What does:
  Enable split-screen on top of COOP Beta that is in GTA Zombie Andreas
Installation:
  * move everyting from GTAZombieAndreasSplitScreen to the game folder. check in both .ini files Enabled=1. Zombie Andreas Split-screen version!



3)IN DEVELOPMENT - DESYNC, BUGS etc. . . . . . . 
_________________________________________________________________________________________________________________________
_
Online Multiplayer:
What does:
  * real across internet or local network multiplayer derived from Split-Screen multiplayer. Host and connect yourselves. In development...
installation:
     move everyting from GTASAOnlineCOOP to the game folder. check in both .ini files Enabled=1. Split-screen version!
Instruction:
  * host server wherever you want: UMServer.bat or UMServer.sh
  * join using UMLauncher.bat
  * configure in UltimateMultiplayerator.ini
