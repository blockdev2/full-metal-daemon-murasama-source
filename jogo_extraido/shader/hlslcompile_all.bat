@echo off
cd /d %~dp0
type nul > error.log

rem --- コンパイル実行
	call :HLSLCompile Sprite
	call :HLSLCompile Particle
	call :HLSLCompile PostEffect
	call :HLSLCompile Scene

rem --- エラー出力確認
	echo;
	cd /d %~dp0
	for %%A in (error.log) do (set /a count=%%~zA)
	if %count% gtr 0 (
		echo ###エラー出力がありました
		type error.log
	) else (
		echo 終了
	)

rem --- 終了
	pause
	exit /b

rem === コンパイル用サブルーチン
:HLSLCompile
	if exist D:\Git\N4System\src\Shader\DX11\%1\HLSLCompile.bat (
		cd /d D:\Git\N4System\src\Shader\DX11\%1
		call D:\Git\N4System\src\Shader\DX11\%1\HLSLCompile.bat %~dp0\%1\ -O3 2>> ..\error.log
	)
	exit /b
